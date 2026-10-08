import copy
import json
import tempfile
import unittest
from pathlib import Path

from contact_open import form_open_verified, open_contact_forms
from controls_probe import WindowActionError
from pinned_groups import consistent_pair
from test_contact_queue import window
from test_pinned_groups import report


def observation(n):
    w=window(n);r=report(w);pair=consistent_pair(r,r,w)
    pair.update(account=f'账号{n}',window=w)
    return pair


def opened(w,*,existing=False):
    return {'ok':True,'scope':'contact_form_open','mode':'open_only','stage':'verified',
        'state':'existing_empty' if existing else 'opened_empty','window_handle':w['hwnd'],
        'process_id':w['pid'],'executable_path':w['path'],'main_runtime_id':f'window:{w["hwnd"]}',
        'dialog_runtime_id':f'dialog:{w["hwnd"]}','contact_runtime_id':f'contact:{w["hwnd"]}',
        'process_path_verified':True,'default_instance_route_verified':True,
        'field_schema_verified':True,'empty_form_verified':True,'launch_requested':not existing,
        'launch_started':not existing,'fields_written':False,'create_clicked':False,
        'contact_created':False,'contact_database_updated':False,'final_invite_clicked':False,'errors':[]}


class ContactOpenTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'opening.json'
        self.records=[observation(1),observation(2)]

    def tearDown(self):self.tmp.cleanup()

    def test_ordered_exact_executable_payload_journal_before_input_and_no_contact_updates(self):
        calls=[];before=copy.deepcopy(self.records)
        def run(w,script,payload):
            saved=json.loads(self.path.read_text())
            i=len(calls);self.assertEqual(saved['jobs'][i]['state'],'opening')
            if i:self.assertEqual(saved['jobs'][i-1]['state'],'empty_form_open')
            self.assertEqual(script,'open_contact.ps1')
            self.assertEqual(payload,{'executable_path':w['path']})
            calls.append(w['hwnd']);return opened(w)
        result=open_contact_forms(self.records,self.path,runner=run)
        self.assertTrue(result['ok']);self.assertEqual(calls,[101,102]);self.assertEqual(before,self.records)
        self.assertEqual(result,json.loads(self.path.read_text()))
        for key in ('fields_written','create_clicked','contact_created','contact_database_updated','final_invite_clicked'):
            self.assertIs(result[key],False)

    def test_existing_empty_form_is_kept_without_reopening(self):
        result=open_contact_forms(self.records[:1],self.path,runner=lambda w,*_:opened(w,existing=True))
        self.assertTrue(result['ok']);self.assertFalse(result['jobs'][0]['opening']['launch_requested'])

    def test_all_identity_empty_and_non_submission_proofs_are_required(self):
        w=window(1)
        self.assertTrue(form_open_verified(opened(w),w))
        changes={'window_handle':102,'process_id':999,'executable_path':window(2)['path'],
            'main_runtime_id':'','dialog_runtime_id':'','contact_runtime_id':'','process_path_verified':False,
            'default_instance_route_verified':False,'field_schema_verified':False,'empty_form_verified':False,
            'fields_written':True,'create_clicked':True,'contact_created':True,'contact_database_updated':True,
            'final_invite_clicked':True,'launch_requested':False,'launch_started':False,'errors':['error'],'stage':'launch'}
        for key,value in changes.items():
            r=opened(w);r[key]=value
            self.assertFalse(form_open_verified(r,w),(key,value))
        r=opened(w,existing=True);r['launch_requested']=True
        self.assertFalse(form_open_verified(r,w))

    def test_uncertain_launch_stops_once_and_preserves_pending_error_report(self):
        calls=[]
        failure=opened(window(1));failure.update(ok=False,state='review',stage='wait_for_form',empty_form_verified=False,
            error='New Contact did not appear in the bound window')
        def run(w,*_):
            calls.append(w['hwnd']);raise WindowActionError('Opening failed',failure)
        result=open_contact_forms(self.records,self.path,runner=run)
        self.assertFalse(result['ok']);self.assertEqual(calls,[101])
        self.assertEqual([j['state'] for j in result['jobs']],['review','not_started'])
        self.assertEqual(result['jobs'][0]['opening'],failure)
        self.assertFalse(result['contact_created'])

    def test_second_failure_preserves_first_empty_form_and_never_retries(self):
        calls=[]
        def run(w,*_):
            calls.append(w['hwnd'])
            if w['hwnd']==102:raise RuntimeError('Existing form is not blank')
            return opened(w)
        result=open_contact_forms(self.records+[observation(3)],self.path,runner=run)
        self.assertEqual(calls,[101,102])
        self.assertEqual([j['state'] for j in result['jobs']],['empty_form_open','review','not_started'])

    def test_invalid_or_duplicate_routing_is_rejected_before_any_script_or_report_write(self):
        cases=[]
        r=copy.deepcopy(self.records);r[1]['window']['path']=r[0]['window']['path'];cases.append(r)
        r=copy.deepcopy(self.records);r[0]['window']['minimized']=True;cases.append(r)
        r=copy.deepcopy(self.records);r[1]['account']=r[0]['account'];cases.append(r)
        for records in cases:
            calls=[]
            with self.assertRaises(ValueError):open_contact_forms(records,self.path,runner=lambda *args:calls.append(args))
            self.assertEqual(calls,[]);self.assertFalse(self.path.exists())

    def test_non_json_report_rejected_before_input(self):
        with self.assertRaises(ValueError):open_contact_forms(self.records,self.path.with_suffix('.txt'),runner=lambda *_:None)
        self.assertFalse(self.path.exists())

    def test_misbound_success_report_cannot_continue_to_next_account(self):
        calls=[]
        def run(w,*_):calls.append(w['hwnd']);return opened(window(2))
        result=open_contact_forms(self.records,self.path,runner=run)
        self.assertFalse(result['ok']);self.assertEqual(calls,[101])
        self.assertEqual(result['jobs'][1]['state'],'not_started')


if __name__=='__main__':unittest.main()
