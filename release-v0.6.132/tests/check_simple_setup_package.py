"""Run from a downloaded ZIP; ephemeral test keys only, no Windows GUI automation."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock, patch
import zipfile

REPOSITORY = Path(__file__).resolve().parents[2]
PACKAGE = REPOSITORY / 'TelegramAssistant-v0.6.132-easy-setup.zip'
POWERSHELL = (Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
              if os.name == 'nt' else Path('/tmp/powershell-7.4.6/pwsh'))


def check():
    assert POWERSHELL.is_file(), 'PowerShell is required for the signing check'
    with tempfile.TemporaryDirectory(prefix='v132-simple-check-') as folder:
        temporary = Path(folder)
        with zipfile.ZipFile(PACKAGE) as archive:
            assert archive.testzip() is None
            assert not any('授权密钥' in n or n.endswith(('准备工具设置.json', '.db', '.csv', '.log', 'activation.json')) for n in archive.namelist())
            archive.extractall(temporary)
        kit = temporary / '平安喜乐助手-v0.6.132-简易准备包'
        tools = kit / '工具文件'
        assert {p.name for p in kit.iterdir()} == {'开始准备新电脑.bat', '先看这个.txt', '工具文件'}
        launcher = (kit / '开始准备新电脑.bat').read_bytes()
        assert '"工具文件\\easy_setup.py"'.encode() in launcher
        sys.path.insert(0, str(tools))
        import easy_setup as easy
        import issuer
        from activation_codec import (ActivationError, display_device, load_public_key,
                                      save_activation, saved_activation_valid, verify_code)
        assert Path(easy.__file__).resolve().parent == tools
        assert Path(issuer.__file__).resolve().parent == tools
        native = issuer.run_rsa
        session = easy.Preparation(tools, kit)
        assert not session.configured()
        template = tools / easy.TEMPLATE
        template_before = hashlib.sha256(template.read_bytes()).hexdigest()
        assert template_before == 'fc999b2c16e2153480f0ef91d208b449208b2dc9c7de4a1478debc5fdb0d6c26'
        devices = [hashlib.sha256(b'ephemeral-test-PC-1').digest(), hashlib.sha256(b'ephemeral-test-PC-2').digest()]
        with patch.object(issuer, 'run_rsa', side_effect=lambda *a, **kw: native(*a, **kw, executable=POWERSHELL)):
            public = session.first_use()
            key_before = issuer.key_path(tools).read_bytes()
            installation = session.prepare_installation()
            assert installation.parent == kit / easy.OUTPUT
            assert (installation.parent / easy.GUIDE).read_bytes() == (tools / easy.GUIDE).read_bytes()
            codes = [session.generate(display_device(device)) for device in devices]
            restarted = easy.Preparation(tools, kit)
            assert restarted.first_use() == public
            assert restarted.generate(display_device(devices[0])) == codes[0]
            assert issuer.key_path(tools).read_bytes() == key_before
        assert hashlib.sha256(template.read_bytes()).hexdigest() == template_before
        with zipfile.ZipFile(installation) as final, zipfile.ZipFile(template) as source:
            assert final.testzip() is None
            names = final.namelist()
            assert not any('授权密钥' in n or 'owner_private' in n or n.endswith(('.db', '.sqlite3', '.csv', '.log', '.xml', '/activation.json')) for n in names)
            root = '平安喜乐助手-v0.6.132/'
            manifest = json.loads(final.read(root + 'installation_manifest.json'))
            assert manifest['version'] == '0.6.132' and manifest['requires_owner_provisioning'] is False
            for name, digest in manifest['files'].items():
                raw = final.read(root + name)
                assert hashlib.sha256(raw).hexdigest() == digest, name
                if name != 'activation_public_key.json':
                    assert raw == source.read(root + name), name
            final.extractall(temporary / 'client')
        client = temporary / 'client/平安喜乐助手-v0.6.132'
        model_manifest = json.loads((client / 'ocr_models/manifest.json').read_text())
        for name, digest in model_manifest.items():
            assert hashlib.sha256((client / 'ocr_models' / name).read_bytes()).hexdigest() == digest
        # Compare the actual customer's business code, not a made-up placeholder.
        personal = Path('/workspace/TelegramAssistant-noactivation')
        for name in ('pinned_members.py', 'ocr_worker.py', 'select_member.ps1', 'store.py'):
            assert (client / name).read_bytes() == (personal / name).read_bytes(), name
        trees = [ast.parse((location / 'app.py').read_text(encoding='utf-8-sig')) for location in (personal, client)]
        classes = [next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'App') for tree in trees]
        assert ast.dump(classes[0]) == ast.dump(classes[1])
        sys.path.insert(0, str(client))
        import app
        from activation_gate import ensure_activated
        from store import Store
        from groups import GroupCatalog
        from pinned_groups import PinnedGroupBindings
        from pinned_members import PinnedMemberPlans
        public_key = load_public_key(client / 'activation_public_key.json')
        data_directories = [temporary / 'PC1-local-data', temporary / 'PC2-local-data']
        for device, code, data in zip(devices, codes, data_directories):
            assert not data.exists()
            verify_code(code, device, public_key)
            save_activation(data / 'activation.json', code, device, public_key)
            assert saved_activation_valid(data / 'activation.json', device, public_key)
            assert not saved_activation_valid(data / 'activation.json', devices[1] if device == devices[0] else devices[0], public_key)
            root = Mock()
            calls = []

            def application(window):
                calls.append(window)
                db = Store(data / 'progress.sqlite3')
                GroupCatalog(db)
                bindings = PinnedGroupBindings(db)
                plans = PinnedMemberPlans(db)
                assert db.rows() == [] and db.next_contact_number() == 1
                assert bindings.accounts() == [] and plans.latest() is None
                db.set_next_number(23 if device == devices[0] else 1)
                db.close()

            with patch('app.tk.Tk', return_value=root), patch('app.DATA', data), patch('app.App', side_effect=application), \
                    patch('activation_gate.device_id', return_value=device), patch('activation_gate._icon'), \
                    patch('activation_gate.tk.Toplevel') as dialog:
                app.main()
                assert calls == [root]
                dialog.assert_not_called()
                root.mainloop.assert_called_once()
            with patch('activation_gate.device_id', return_value=device), patch('activation_gate._icon'), \
                    patch('activation_gate.tk.Toplevel') as dialog:
                assert ensure_activated(Mock(), client, data)
                dialog.assert_not_called()
        for directory, expected in zip(data_directories, (23, 1)):
            db = Store(directory / 'progress.sqlite3')
            assert db.next_contact_number() == expected
            db.close()
    print('PASS: downloaded simple ZIP and launcher structure; bundled original v132 template unchanged.')
    print('PASS: actual PowerShell RSA; one-time preparation; saved authority and existing activation code preserved.')
    print('PASS: automatic installer and short guide output; all business file, manifest and OCR weight checksums.')
    print('PASS: final installer contains no owner keys, usage records or saved activation.')
    print('PASS: two distinct devices and independent databases; activation stays valid after restart.')
    print('Windows GUI input was simulated; live Windows software installation and Telegram require the destination PC.')


if __name__ == '__main__':
    check()
