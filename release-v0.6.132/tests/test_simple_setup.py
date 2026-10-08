"""Safety and workflow checks for the owner-side convenience wrapper."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'simple-setup'))
import easy_setup as easy
from activation_codec import ActivationError, display_device, public_key_from_json, verify_code

VECTORS = json.loads((HERE / 'test_fixtures' / 'activation_vectors.json').read_text())
POWERSHELL = (Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
              if os.name == 'nt' else Path('/tmp/powershell-7.4.6/pwsh'))


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name) / '简易准备包'
        self.base.mkdir()
        self.session = easy.Preparation(self.base)
        self.old = self.base.parent / '原激活工具'
        key = easy.issuer.key_path(self.old)
        key.parent.mkdir(parents=True)
        key.write_bytes(b'existing signing authority')

    def remember_old(self):
        with patch.object(easy.issuer, 'authority_public', return_value=VECTORS['public']):
            return self.session.use_existing(self.old)

    def test_fresh_package_has_no_authority_until_explicit_first_use(self):
        self.assertFalse(self.session.configured())
        with patch.object(easy.issuer, 'initialize_authority') as initialize:
            with self.assertRaises(ActivationError):
                self.session.public()
            initialize.assert_not_called()
        with patch.object(easy.issuer, 'initialize_authority', return_value=VECTORS['public']) as initialize:
            self.session.first_use()
            initialize.assert_called_once_with(self.base)
        settings = json.loads((self.base / easy.SETTINGS).read_text())
        self.assertEqual(settings['authority_directory'], '.')

    def test_reusing_old_folder_does_not_generate_copy_or_replace_keys(self):
        original = easy.issuer.key_path(self.old).read_bytes()
        with patch.object(easy.issuer, 'initialize_authority') as initialize:
            self.remember_old()
            with patch.object(easy.issuer, 'authority_public', return_value=VECTORS['public']):
                easy.Preparation(self.base).first_use()
            initialize.assert_not_called()
        self.assertEqual(easy.issuer.key_path(self.old).read_bytes(), original)
        self.assertFalse(easy.issuer.key_path(self.base).exists())
        self.assertEqual(self.session._selection()[0], self.old)

    def test_selecting_key_subfolder_remembers_its_owner_folder(self):
        with patch.object(easy.issuer, 'authority_public', return_value=VECTORS['public']):
            self.session.use_existing(self.old / '授权密钥')
        self.assertEqual(self.session._selection()[0], self.old)

    def test_lost_old_key_and_invalid_settings_never_create_new_keys(self):
        self.remember_old()
        easy.issuer.key_path(self.old).unlink()
        with patch.object(easy.issuer, 'initialize_authority') as initialize:
            with self.assertRaises(ActivationError):
                self.session.first_use()
            initialize.assert_not_called()
        (self.base / easy.SETTINGS).write_text('not json')
        with patch.object(easy.issuer, 'initialize_authority') as initialize:
            with self.assertRaises(ActivationError):
                self.session.first_use()
            initialize.assert_not_called()

    def test_missing_local_key_after_initialization_is_not_recreated(self):
        marker = easy.issuer.key_path(self.base).parent / 'authority_initialized.json'
        marker.parent.mkdir()
        marker.write_text('{}')
        with patch.object(easy.issuer, 'initialize_authority') as initialize:
            with self.assertRaises(ActivationError):
                self.session.first_use()
            initialize.assert_not_called()

    def test_replaced_key_is_rejected_before_installation_or_signing(self):
        self.remember_old()
        changed_public = dict(VECTORS['public'], exponent='Aw==')
        with patch.object(easy.issuer, 'authority_public', return_value=changed_public), \
                patch.object(easy.issuer, 'issue_activation') as sign:
            with self.assertRaises(ActivationError):
                self.session.generate(display_device(bytes.fromhex(VECTORS['device'])))
            sign.assert_not_called()

    def test_bad_device_never_requests_authority_or_signs(self):
        with patch.object(self.session, 'public') as public, patch.object(easy.issuer, 'issue_activation') as sign:
            with self.assertRaises(ActivationError):
                self.session.generate('not a device code')
            public.assert_not_called()
            sign.assert_not_called()

    def test_invalid_old_folder_does_not_save_settings(self):
        with self.assertRaises(ActivationError):
            self.session.use_existing(self.base.parent / 'wrong-folder')
        self.assertFalse((self.base / easy.SETTINGS).exists())

    def test_output_names_are_automatic_and_previous_installer_is_preserved(self):
        self.remember_old()
        template = self.base / easy.TEMPLATE
        template.parent.mkdir()
        template.write_bytes(b'installer template')
        (self.base / easy.GUIDE).write_bytes(b'short installation steps')
        def build(source, output, public):
            self.assertEqual(source, template)
            self.assertEqual(public, VECTORS['public'])
            self.assertFalse(output.exists())
            output.write_bytes(b'prepared installer')
        with patch.object(easy.issuer, 'authority_public', return_value=VECTORS['public']), \
                patch.object(easy.issuer, 'build_installation', side_effect=build):
            first = self.session.prepare_installation()
            original = first.read_bytes()
            second = self.session.prepare_installation()
        self.assertEqual(first.name, easy.INSTALLER)
        self.assertEqual(second.name, Path(easy.INSTALLER).stem + '-2.zip')
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual((first.parent / easy.GUIDE).read_bytes(), b'short installation steps')

    def test_missing_template_or_guide_does_not_produce_partial_installer(self):
        self.remember_old()
        with patch.object(easy.issuer, 'authority_public', return_value=VECTORS['public']), \
                patch.object(easy.issuer, 'build_installation') as build:
            with self.assertRaises(ActivationError):
                self.session.prepare_installation()
            template = self.base / easy.TEMPLATE
            template.parent.mkdir()
            template.write_bytes(b'template')
            with self.assertRaises(OSError):
                self.session.prepare_installation()
            build.assert_not_called()
        self.assertFalse((self.base / easy.OUTPUT).exists())

    def test_local_authority_survives_moving_the_whole_preparation_folder(self):
        local_key = easy.issuer.key_path(self.base)
        local_key.parent.mkdir()
        local_key.write_bytes(b'original local signing key')
        with patch.object(easy.issuer, 'authority_public', return_value=VECTORS['public']):
            self.session.public()
            moved = self.base.with_name('moved preparation')
            self.base.rename(moved)
            self.assertEqual(easy.Preparation(moved).public(), VECTORS['public'])
        self.assertEqual(easy.issuer.key_path(moved).read_bytes(), b'original local signing key')


class ShortcutTests(unittest.TestCase):
    def test_successful_generate_copies_code_in_same_action_without_a_file_dialog(self):
        tool = easy.SimpleSetup.__new__(easy.SimpleSetup)
        device = display_device(bytes.fromhex(VECTORS['device']))
        tool.device = Mock()
        tool.device.get.return_value = device
        tool.preparation = Mock()
        tool.preparation.generate.return_value = 'expected activation code'
        tool._initial_choice = Mock(return_value=('ready', None))
        tool._set_code = Mock()
        tool.copy = Mock()
        tool._run = lambda operation, success, status: success(operation())
        with patch.object(easy.filedialog, 'askopenfilename') as open_dialog, \
                patch.object(easy.filedialog, 'asksaveasfilename') as save_dialog:
            tool.generate()
            open_dialog.assert_not_called()
            save_dialog.assert_not_called()
        tool.preparation.generate.assert_called_once_with(device)
        tool._set_code.assert_called_with('expected activation code')
        tool.copy.assert_called_once()

    def test_ready_authority_does_not_show_first_use_dialog_again(self):
        tool = easy.SimpleSetup.__new__(easy.SimpleSetup)
        tool.preparation = Mock()
        tool.preparation.configured.return_value = True
        with patch.object(easy.tk, 'Toplevel') as dialog:
            self.assertEqual(tool._initial_choice(), ('ready', None))
            dialog.assert_not_called()

    def test_successful_package_opens_output_without_selecting_template_or_save_path(self):
        tool = easy.SimpleSetup.__new__(easy.SimpleSetup)
        tool.preparation = Mock()
        tool.preparation.prepare_installation.return_value = Path(easy.INSTALLER)
        tool._initial_choice = Mock(return_value=('ready', None))
        tool.open_output = Mock()
        tool.status = Mock()
        tool._run = lambda operation, success, status: success(operation())
        with patch.object(easy.filedialog, 'askopenfilename') as open_dialog, \
                patch.object(easy.filedialog, 'asksaveasfilename') as save_dialog:
            tool.package()
            open_dialog.assert_not_called()
            save_dialog.assert_not_called()
        tool.preparation.prepare_installation.assert_called_once()
        tool.open_output.assert_called_once()


@unittest.skipUnless(POWERSHELL.is_file(), 'PowerShell required for actual RSA')
class ActualAuthorityTests(unittest.TestCase):
    def test_old_key_old_code_and_new_device_codes_remain_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old = base / '原工具'
            simple = base / '简易准备'
            simple.mkdir()
            native = easy.issuer.run_rsa
            with patch.object(easy.issuer, 'run_rsa', side_effect=lambda *a, **kw: native(*a, **kw, executable=POWERSHELL)):
                public = easy.issuer.initialize_authority(old)
                key_before = easy.issuer.key_path(old).read_bytes()
                device = bytes.fromhex(VECTORS['device'])
                old_code = easy.issuer.issue_activation(old, display_device(device))
                session = easy.Preparation(simple)
                self.assertEqual(session.use_existing(old), public)
                self.assertEqual(easy.Preparation(simple).first_use(), public)
                new_code = easy.Preparation(simple).generate(display_device(device))
                self.assertEqual(old_code, new_code)
                self.assertEqual(easy.issuer.key_path(old).read_bytes(), key_before)
                self.assertFalse(easy.issuer.key_path(simple).exists())
                other_device = bytes.fromhex(VECTORS['other_device'])
                other_code = session.generate(display_device(other_device))
                verify_code(other_code, other_device, public_key_from_json(public))
                with self.assertRaises(ActivationError):
                    verify_code(other_code, device, public_key_from_json(public))


if __name__ == '__main__':
    unittest.main()
