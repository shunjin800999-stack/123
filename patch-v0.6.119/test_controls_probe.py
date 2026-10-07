import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from controls_probe import summarize, fill_contact_test, verify_contact_profile, inspect_member_visual


class ControlReportTests(unittest.TestCase):
    def test_header_diagnostic_is_opt_in_and_does_not_replace_selection_evidence(self):
        from test_visual_members import VisualMemberTests
        for enabled in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'visual.png'
                r=VisualMemberTests().report()
                r['header_diagnostic']={'available':True,'text':'1 2','used_for_selection':False}
                with patch('controls_probe.run_window_script',return_value=r) as run:
                    result=inspect_member_visual({},path,'2',header_diagnostic=enabled)
                self.assertEqual(run.call_args.args[1],'inspect_member_visual.ps1')
                self.assertEqual(run.call_args.args[2].get('header_diagnostic',False),enabled)
                self.assertEqual(result['analysis']['selected_numbers'],[])
                self.assertEqual(result['analysis']['chip_matches'],0)
                self.assertTrue(path.with_suffix('.json').exists())

    def test_large_script_is_loaded_from_file_with_short_utf8_bootstrap(self):
        import json,subprocess
        from types import SimpleNamespace
        from controls_probe import run_window_script
        from test_contact_queue import window
        w=window(1)
        completed=subprocess.CompletedProcess([],0,stdout=json.dumps({'ok':True}).encode(),stderr=b'')
        with patch('controls_probe.Path.read_text',return_value='#'*40000),patch('controls_probe.os',SimpleNamespace(name='nt',environ={})),patch('controls_probe.scan_windows',return_value=[w]),patch('controls_probe.Path.is_file',return_value=True),patch('controls_probe.subprocess.CREATE_NO_WINDOW',0,create=True),patch('controls_probe.subprocess.run',return_value=completed) as run:
            report=run_window_script(w,'submit_contact.ps1',{'mode':'open','username':'@test_user'})
        args=run.call_args.args[0];env=run.call_args.kwargs['env']
        self.assertLess(sum(len(str(a)) for a in args),1024)
        self.assertIn('[Text.Encoding]::UTF8',args[-1])
        self.assertIn('$env:TG_ACTION_SCRIPT_NAME',args[-1])
        self.assertNotIn('function ReopenSubmittedProfile',args[-1])
        self.assertEqual(env['TG_ACTION_SCRIPT_NAME'],'submit_contact.ps1')
        self.assertEqual(json.loads(env['TG_ACTION_PAYLOAD'])['username'],'@test_user')
        self.assertTrue(report['ok'])

    def profile(self):
        return {'ok':True,'read_only':True,'scope':'profile','truncated':False,'errors':[],
                'controls':[{'type':'Text','name':'001','visible':True},
                            {'type':'Text','name':'+55 (16) 99123-4567','visible':True},
                            {'type':'Button','name':'Edit contact','visible':True,'enabled':True},
                            {'type':'Button','name':'Delete contact','visible':True,'enabled':True}]}

    def test_profile_requires_phone_name_and_both_saved_contact_markers(self):
        report=self.profile()
        self.assertTrue(verify_contact_profile(report,'+5516991234567','001')['verified'])
        for name in ('001','+55 (16) 99123-4567','Edit contact','Delete contact'):
            partial=self.profile()
            partial['controls']=[row for row in partial['controls'] if row['name']!=name]
            self.assertFalse(verify_contact_profile(partial,'+5516991234567','001')['verified'])

    def test_plain_and_legacy_profile_names_are_checked_exactly(self):
        report=self.profile()
        self.assertFalse(verify_contact_profile(report,'+5516991234567',1)['verified'])
        self.assertTrue(verify_contact_profile(report,'+5516991234567','001')['verified'])
        report['controls'][0]['name']='1'
        self.assertTrue(verify_contact_profile(report,'+5516991234567',1)['verified'])
        self.assertFalse(verify_contact_profile(report,'+5516991234567','001')['verified'])

    def test_portuguese_saved_contact_markers_require_visible_enabled_exact_buttons(self):
        from copy import deepcopy
        report=self.profile()
        report['controls'][2]['name']='Editar contato'
        report['controls'][3]['name']='Apagar contato'
        self.assertTrue(verify_contact_profile(report,'+5516991234567','001')['verified'])
        for index in (2,3):
            for key,value in (('visible',False),('enabled',False),('type','Text'),('name','Compartilhar este contato')):
                partial=deepcopy(report)
                partial['controls'][index][key]=value
                self.assertFalse(verify_contact_profile(partial,'+5516991234567','001')['verified'],(index,key))

    def test_other_person_hidden_text_incomplete_or_chat_report_cannot_prove_add(self):
        for mutation in ('other_phone','other_number','hidden','window','truncated','errors','not_read_only'):
            report=self.profile()
            if mutation=='other_phone':report['controls'][1]['name']='+5516991234568'
            elif mutation=='other_number':report['controls'][0]['name']='002'
            elif mutation=='hidden':report['controls'][1]['visible']=False
            elif mutation=='window':report['scope']='window'
            elif mutation=='truncated':report['truncated']=True
            elif mutation=='errors':report['errors']=['unavailable']
            else:report['read_only']=False
            self.assertFalse(verify_contact_profile(report,'+5516991234567','001')['verified'],mutation)

    def test_incomplete_profile_reports_observed_matches_but_does_not_confirm(self):
        report=self.profile();report['truncated']=True
        result=verify_contact_profile(report,'+5516991234567','001')
        self.assertFalse(result['verified'])
        self.assertFalse(result['checks']['profile'])
        self.assertTrue(all(value for key,value in result['checks'].items() if key!='profile'))

    def test_summary_distinguishes_visible_edit_and_hidden_controls(self):
        report={'controls':[
            {'type':'Edit','name':'First name','offscreen':False,'enabled':True,'value_pattern':True},
            {'type':'Edit','name':'hidden','offscreen':True},
            {'type':'Button','name':'Create','offscreen':False,'enabled':True},
        ]}
        text=summarize(report)
        self.assertIn('可见输入框 1 个',text)
        self.assertIn('First name',text)
        self.assertIn('Create',text)
        self.assertNotIn('hidden',text)

    def test_empty_or_truncated_report_does_not_claim_usable_input(self):
        text=summarize({'controls':[],'truncated':True,'errors':['unavailable']})
        self.assertIn('未读取到可见输入框',text)
        self.assertIn('可能不完整',text)
        self.assertIn('unavailable',text)

    def test_fill_validates_payload_before_requesting_window_operation(self):
        with patch('controls_probe.run_window_script') as run:
            for phone,number in [('not-a-phone',1),('+5516991234567',0),('+5516991234567',1000000)]:
                with self.assertRaises(ValueError):fill_contact_test({},phone,number)
            run.assert_not_called()

    def test_fill_uses_normalized_phone_and_numeric_name(self):
        window={'hwnd':100,'pid':200,'path':r'C:\Apps\Telegram.exe'}
        with patch('controls_probe.run_window_script',return_value={'ok':True}) as run:
            result=fill_contact_test(window,'+55 (16) 99123-4567',7)
            self.assertTrue(result['ok'])
            run.assert_called_once_with(window,'fill_contact.ps1',{'phone':'+5516991234567','number':'7'})


if __name__=='__main__':unittest.main(verbosity=2)
