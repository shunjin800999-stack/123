import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from activation_codec import (ActivationError, make_code, public_key_from_json,
                              signing_payload, verify_code)

SPEC = importlib.util.spec_from_file_location('tested_activation_issuer', Path(__file__).parent / 'activation_owner' / 'issuer.py')
issuer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issuer)
VECTORS = json.loads((Path(__file__).parent / 'test_fixtures' / 'activation_vectors.json').read_text())


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.root = '平安喜乐助手-v0.6.117'
        self.files = {'app.py': b'# current unchanged business logic\n',
                      'START.bat': b'@echo off\n', 'ocr_models/manifest.json': b'{}'}

    def template(self, corrupt=None, extra=None):
        path = self.base / 'template.zip'
        manifest = {'format': issuer.INSTALL_FORMAT, 'requires_owner_provisioning': True,
                    'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in self.files.items()}}
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(self.root + '/activation_template.json', json.dumps({'format': issuer.TEMPLATE_FORMAT, 'version': '0.6.117'}))
            archive.writestr(self.root + '/installation_manifest.json', json.dumps(manifest))
            for name, raw in self.files.items():
                archive.writestr(self.root + '/' + name, b'tampered' if name == corrupt else raw)
            if extra:
                archive.writestr(self.root + '/' + extra, 'not allowed')
        return path

    def test_owner_provisioned_installation_contains_only_public_key_and_original_files(self):
        template = self.template(); original = template.read_bytes()
        output = self.base / 'final.zip'
        issuer.build_installation(template, output, VECTORS['public'])
        self.assertEqual(template.read_bytes(), original)
        with zipfile.ZipFile(output) as archive:
            for name, raw in self.files.items():
                self.assertEqual(archive.read(self.root + '/' + name), raw)
            public = json.loads(archive.read(self.root + '/activation_public_key.json'))
            self.assertEqual(public, VECTORS['public'])
            names = archive.namelist()
            self.assertFalse(any('private' in name or 'activation.json' in name for name in names))
            self.assertNotIn(self.root + '/activation_template.json', names)
            manifest = json.loads(archive.read(self.root + '/installation_manifest.json'))
            self.assertFalse(manifest['requires_owner_provisioning'])
            for name, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256(archive.read(self.root + '/' + name)).hexdigest(), digest)

    def test_tampered_and_extra_database_files_are_rejected(self):
        output = self.base / 'final.zip'
        for template in (self.template(corrupt='app.py'),):
            with self.assertRaises(ActivationError):
                issuer.build_installation(template, output, VECTORS['public'])
            self.assertFalse(output.exists())
        template = self.template(extra='progress.sqlite3')
        with self.assertRaises(ActivationError):
            issuer.build_installation(template, output, VECTORS['public'])
        self.assertFalse(output.exists())
        self.assertEqual(list(self.base.glob('*.tmp')), [])

    def test_signed_or_listed_records_and_path_traversal_still_rejected(self):
        for forbidden in ('progress.sqlite3', '使用记录.csv', 'owner_private_key.xml', '../escape.py'):
            self.files[forbidden] = b'unwanted file'
            template = self.template()
            with self.assertRaises(ActivationError):
                issuer.build_installation(template, self.base / 'final.zip', VECTORS['public'])
            del self.files[forbidden]

    def test_existing_output_and_original_template_are_not_overwritten(self):
        template = self.template(); original = template.read_bytes()
        with self.assertRaises(ActivationError):
            issuer.build_installation(template, template, VECTORS['public'])
        self.assertEqual(template.read_bytes(), original)
        output = self.base / 'final.zip'; output.write_bytes(b'previous installation')
        with self.assertRaises(ActivationError):
            issuer.build_installation(template, output, VECTORS['public'])
        self.assertEqual(output.read_bytes(), b'previous installation')

    def test_non_template_and_invalid_public_keys_are_rejected(self):
        path = self.base / 'other.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('app.py', 'ordinary archive')
        with self.assertRaises(ActivationError):
            issuer.build_installation(path, self.base / 'final.zip', VECTORS['public'])
        with self.assertRaises(ActivationError):
            issuer.build_installation(self.template(), self.base / 'final.zip', {})

    def test_lost_existing_owner_key_is_not_reinitialized(self):
        marker = issuer.key_path(self.base).parent / 'authority_initialized.json'
        marker.parent.mkdir(); marker.write_text('{}')
        with patch.object(issuer, 'run_rsa') as operation:
            with self.assertRaises(ActivationError):
                issuer.initialize_authority(self.base)
            operation.assert_not_called()

    def test_malformed_device_code_never_signs(self):
        with patch.object(issuer, 'run_rsa') as operation:
            with self.assertRaises(ActivationError):
                issuer.issue_activation(self.base, 'incorrect device code')
            operation.assert_not_called()


POWERSHELL = (Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
              if os.name == 'nt' else Path('/tmp/powershell-7.4.6/pwsh'))


@unittest.skipUnless(POWERSHELL.is_file(), 'PowerShell is not installed for RSA interoperability checks')
class RealSigningTests(unittest.TestCase):
    def test_init_repeat_sign_and_verify_with_actual_dotnet_rsa(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            original_operation = issuer.run_rsa
            def actual_operation(*args, **kwargs):
                return original_operation(*args, **kwargs, executable=POWERSHELL)
            with patch.object(issuer, 'run_rsa', side_effect=actual_operation):
                public = issuer.initialize_authority(base)
                original_key = issuer.key_path(base).read_bytes()
                self.assertEqual(issuer.initialize_authority(base), public)
                self.assertEqual(issuer.key_path(base).read_bytes(), original_key)
                device = bytes.fromhex(VECTORS['device'])
                from activation_codec import display_device
                code = issuer.issue_activation(base, display_device(device))
                self.assertEqual(verify_code(code, device, public_key_from_json(public)), code)
                with self.assertRaises(ActivationError):
                    verify_code(code, bytes.fromhex(VECTORS['other_device']), public_key_from_json(public))

