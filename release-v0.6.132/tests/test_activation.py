import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import app
from activation_codec import (ActivationError, device_id, display_device, load_public_key,
                              make_code, parse_device, public_key_from_json,
                              save_activation, saved_activation_valid, verify_code)
from activation_gate import ensure_activated


VECTORS = json.loads((Path(__file__).parent / 'test_fixtures' / 'activation_vectors.json').read_text())


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.device = bytes.fromhex(VECTORS['device'])
        self.other = bytes.fromhex(VECTORS['other_device'])
        self.public = public_key_from_json(VECTORS['public'])
        self.code = VECTORS['code']

    def test_actual_powershell_signature_is_accepted(self):
        self.assertEqual(verify_code(self.code, self.device, self.public), self.code)

    def test_device_copy_and_wrong_issuer_are_rejected(self):
        with self.assertRaises(ActivationError):
            verify_code(self.code, self.other, self.public)
        with self.assertRaises(ActivationError):
            verify_code(VECTORS['different_issuer_code'], self.device, self.public)

    def test_modified_signature_and_forged_unsigned_code_are_rejected(self):
        fields = self.code.split('.')
        signature = bytearray(base64.urlsafe_b64decode(fields[2] + '=' * (-len(fields[2]) % 4)))
        signature[12] ^= 1
        for code in (make_code(self.device, bytes(signature)), make_code(self.device, bytes(256))):
            with self.assertRaises(ActivationError):
                verify_code(code, self.device, self.public)

    def test_malformed_and_oversized_codes_are_rejected(self):
        for code in ('', None, 'PAK1.bad.bad', 'x' * 3000, self.code + '.', self.code[:-5]):
            with self.subTest(code=str(code)[:20]):
                with self.assertRaises(ActivationError):
                    verify_code(code, self.device, self.public)

    def test_copy_paste_line_breaks_are_accepted(self):
        pasted = '\n  ' + '\n'.join(self.code[i:i + 60] for i in range(0, len(self.code), 60)) + '\r\n'
        self.assertEqual(verify_code(pasted, self.device, self.public), self.code)

    def test_device_display_and_normalized_input(self):
        displayed = display_device(self.device)
        self.assertEqual(parse_device(displayed), self.device)
        self.assertEqual(parse_device('  ' + displayed.lower() + '\n'), self.device)
        for value in ('', self.code, displayed[:-1], 'PA1-' + 'Z' * 64):
            with self.assertRaises(ActivationError):
                parse_device(value)

    def test_missing_corrupt_and_nonpublic_keys_are_rejected(self):
        path = self.base / 'public.json'
        with self.assertRaises(ActivationError):
            load_public_key(path)
        for value in ({}, {'format': 'wrong'}, {'format': VECTORS['public']['format'], 'modulus': 'bad'}):
            path.write_text(json.dumps(value))
            with self.assertRaises(ActivationError):
                load_public_key(path)
        value = copy.deepcopy(VECTORS['public'])
        value['exponent'] = 'Aw=='
        with self.assertRaises(ActivationError):
            public_key_from_json(value)

    def test_first_save_then_restart_skips_activation_dialog(self):
        public_path = self.base / 'activation_public_key.json'
        public_path.write_text(json.dumps(VECTORS['public']))
        data = self.base / 'local-data'
        save_activation(data / 'activation.json', self.code, self.device, self.public)
        with patch('activation_gate.device_id', return_value=self.device), patch('activation_gate._icon'), \
             patch('activation_gate.tk.Toplevel') as dialog:
            self.assertTrue(ensure_activated(Mock(), self.base, data))
            dialog.assert_not_called()
        self.assertEqual([p.name for p in data.iterdir()], ['activation.json'])

    def test_wrong_device_and_tampered_record_need_activation_again(self):
        path = self.base / 'activation.json'
        save_activation(path, self.code, self.device, self.public)
        self.assertFalse(saved_activation_valid(path, self.other, self.public))
        path.write_text(json.dumps({'format': 'pinganxile-activation-v1', 'code': 'forged'}))
        self.assertFalse(saved_activation_valid(path, self.device, self.public))
        path.write_text('broken JSON')
        self.assertFalse(saved_activation_valid(path, self.device, self.public))

    def test_invalid_code_never_creates_activation_or_database(self):
        path = self.base / 'new' / 'activation.json'
        with self.assertRaises(ActivationError):
            save_activation(path, self.code, self.other, self.public)
        self.assertFalse(path.parent.exists())

    def test_missing_public_key_blocks_without_opening_window_or_database(self):
        with patch('activation_gate.device_id', return_value=self.device), patch('activation_gate._icon'), \
             patch('activation_gate.messagebox.showerror'), patch('activation_gate.tk.Toplevel') as dialog:
            self.assertFalse(ensure_activated(Mock(), self.base, self.base / 'local-data'))
            dialog.assert_not_called()
        self.assertFalse((self.base / 'local-data').exists())

    def test_windows_machine_id_is_read_only_and_deterministic(self):
        key = Mock()
        registry = Mock()
        registry.OpenKey.return_value.__enter__ = Mock(return_value=key)
        registry.OpenKey.return_value.__exit__ = Mock(return_value=False)
        registry.KEY_READ = 1; registry.KEY_WOW64_64KEY = 256
        registry.QueryValueEx.return_value = ('TEST-WINDOWS-INSTALLATION-GUID', 1)
        with patch('activation_codec.os.name', 'nt'), patch.dict('sys.modules', {'winreg': registry}):
            first = device_id()
            self.assertEqual(device_id(), first)
            registry.QueryValueEx.return_value = ('OTHER-WINDOWS-INSTALLATION-GUID', 1)
            self.assertNotEqual(device_id(), first)
        registry.SetValueEx.assert_not_called()

    def test_cancelled_activation_never_constructs_app_or_store(self):
        root = Mock()
        with patch('app.tk.Tk', return_value=root), patch('activation_gate.ensure_activated', return_value=False), \
             patch('app.App') as application, patch('app.Store') as store:
            app.main()
        application.assert_not_called(); store.assert_not_called()
        root.deiconify.assert_not_called(); root.mainloop.assert_not_called()
        root.destroy.assert_called_once()

    def test_valid_activation_enters_original_app(self):
        root = Mock(); calls = []
        def authorized(*args):
            calls.append('authorize'); return True
        def application(*args):
            calls.append('app')
        with patch('app.tk.Tk', return_value=root), patch('activation_gate.ensure_activated', side_effect=authorized), \
             patch('app.App', side_effect=application):
            app.main()
        self.assertEqual(calls, ['authorize', 'app'])
        root.deiconify.assert_called_once(); root.mainloop.assert_called_once()

