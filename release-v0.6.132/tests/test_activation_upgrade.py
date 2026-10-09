"""Existing signing authority can provision the v132 business snapshot."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from activation_codec import ActivationError,display_device,public_key_from_json,verify_code
import test_activation_issuer as activation_tests

BASE=Path(__file__).parent
ORIGINAL=Path('/workspace/123/release-v0.6.117')
PERSONAL=Path('/workspace/TelegramAssistant-noactivation')
issuer=activation_tests.issuer
PUBLIC=activation_tests.VECTORS['public']


class ActivationUpgradeTests(unittest.TestCase):
    def template(self,folder,version,manifest_version=None):
        path=folder/'template.zip';root='平安喜乐助手-v'+version
        files={'app.py':b'# approved business snapshot\n','START.bat':b'@echo off\n'}
        manifest={'format':issuer.INSTALL_FORMAT,'version':manifest_version or version,
            'requires_owner_provisioning':True,'files':{n:hashlib.sha256(b).hexdigest() for n,b in files.items()}}
        with zipfile.ZipFile(path,'w') as z:
            for name,raw in files.items():z.writestr(root+'/'+name,raw)
            z.writestr(root+'/installation_manifest.json',json.dumps(manifest))
            z.writestr(root+'/activation_template.json',json.dumps({'format':issuer.TEMPLATE_FORMAT,'version':version}))
        return path,root,files

    def test_v132_template_is_provisioned_without_changing_business_files(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);path,root,files=self.template(folder,'0.6.132');out=folder/'final.zip'
            issuer.build_installation(path,out,PUBLIC)
            with zipfile.ZipFile(out) as z:
                for name,raw in files.items():self.assertEqual(z.read(root+'/'+name),raw)
                self.assertEqual(json.loads(z.read(root+'/activation_public_key.json')),PUBLIC)
                manifest=json.loads(z.read(root+'/installation_manifest.json'))
                self.assertEqual(manifest['version'],'0.6.132');self.assertFalse(manifest['requires_owner_provisioning'])
                self.assertFalse(any('private' in n or n.endswith('/activation.json') for n in z.namelist()))

    def test_original_v117_templates_remain_compatible(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);path,_,_=self.template(folder,'0.6.117')
            issuer.build_installation(path,folder/'final.zip',PUBLIC)
            self.assertTrue((folder/'final.zip').is_file())

    def test_version_mismatch_and_unknown_template_fail_without_output(self):
        for version,manifest_version in (('0.6.132','0.6.117'),('0.6.131','0.6.131')):
            with self.subTest(version=version,manifest=manifest_version),tempfile.TemporaryDirectory() as d:
                folder=Path(d);path,_,_=self.template(folder,version,manifest_version);out=folder/'final.zip'
                with self.assertRaises(ActivationError):issuer.build_installation(path,out,PUBLIC)
                self.assertFalse(out.exists());self.assertEqual(list(folder.glob('*.tmp')),[])

    def test_approved_v132_app_differs_only_in_main_activation_gate(self):
        personal=(PERSONAL/'app.py').read_text(encoding='utf-8-sig');activated=(BASE/'app.py').read_text(encoding='utf-8-sig')
        before=ast.parse(personal);after=ast.parse(activated)
        strip=lambda tree:ast.Module(body=[n for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name!='main'],type_ignores=[])
        self.assertEqual(ast.dump(strip(before)),ast.dump(strip(after)))
        main=lambda text:ast.get_source_segment(text,next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='main'))
        self.assertEqual(activated.replace(main(activated),main(personal)),personal)
        excluded={'app.py','archive_before_cutoff.py','reset_current_invitation.py','reset_unsubmitted.py','offline_pause_test.py'}
        for p in PERSONAL.iterdir():
            if p.is_file() and p.suffix in ('.py','.ps1') and not p.name.startswith('test_') and p.name not in excluded:
                self.assertEqual((BASE/p.name).read_bytes(),p.read_bytes(),p.name)

    def test_crypto_and_first_activation_logic_are_unchanged(self):
        for name in ('activation_codec.py','activation_gate.py'):
            self.assertEqual((BASE/name).read_bytes(),(ORIGINAL/'client'/name).read_bytes())
        old=(ORIGINAL/'owner-tool/issuer.py').read_text();new=(BASE/'activation_owner/issuer.py').read_text()
        functions=lambda text:{n.name:ast.get_source_segment(text,n) for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
        a,b=functions(old),functions(new)
        for name in ('run_rsa','key_path','authority_public','initialize_authority','issue_activation'):
            self.assertEqual(a[name],b[name],name)

    @unittest.skipUnless(activation_tests.POWERSHELL.is_file(),'PowerShell required for actual RSA signing')
    def test_upgrading_tool_preserves_old_key_and_existing_device_code(self):
        spec=importlib.util.spec_from_file_location('old_upgrade_test_issuer',ORIGINAL/'owner-tool/issuer.py')
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        def actual(module):
            original=module.run_rsa
            return patch.object(module,'run_rsa',side_effect=lambda *args,**kwargs:original(*args,**kwargs,executable=activation_tests.POWERSHELL))
        with tempfile.TemporaryDirectory() as d,actual(old),actual(issuer):
            folder=Path(d);public=old.initialize_authority(folder);original_key=old.key_path(folder).read_bytes()
            device=bytes.fromhex(activation_tests.VECTORS['device']);code=old.issue_activation(folder,display_device(device))
            self.assertEqual(issuer.initialize_authority(folder),public)
            self.assertEqual(issuer.key_path(folder).read_bytes(),original_key)
            self.assertEqual(issuer.issue_activation(folder,display_device(device)),code)
            self.assertEqual(verify_code(code,device,public_key_from_json(public)),code)
            other=bytes.fromhex(activation_tests.VECTORS['other_device'])
            with self.assertRaises(ActivationError):verify_code(code,other,public_key_from_json(public))
            path,root,_=self.template(folder,'0.6.132');issuer.build_installation(path,folder/'final.zip',public)
            with zipfile.ZipFile(folder/'final.zip') as z:
                installed=public_key_from_json(json.loads(z.read(root+'/activation_public_key.json')))
                self.assertEqual(verify_code(code,device,installed),code)


if __name__=='__main__':unittest.main()
