import copy
import json
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from group_navigation import open_pinned_groups, navigation_entries, navigation_verified
from controls_probe import WindowActionError
from groups import GroupCatalog
from pinned_groups import PinnedGroupBindings, pinned_pair
from store import Store
from test_contact_queue import window
from test_pinned_groups import report


def observation(i=1, name='A'):
    w=window(i);r=report(w,portuguese=i==2)
    pair=pinned_pair(r,w,require_fields=True)
    pair.update(account=name,window=w)
    return pair


def opened(record,slot):
    w=record['window'];g=record['targets'][slot-1]
    return {'ok':True,'scope':'pinned_group_navigation','state':'members_open','errors':[],
        'window_handle':w['hwnd'],'process_id':w['pid'],'slot':slot,'group_name':g['name'],
        'group_runtime_id':g['runtime_id'],'profile_verified':True,'profile_runtime_id':f'profile:{w["hwnd"]}',
        'dialog_runtime_id':f'dialog:{w["hwnd"]}:{slot}','final_invite_clicked':False,
        'members_selected':False,'contact_database_updated':False,
        'actions':[{'action':a,'attempted':True,'invoked':True} for a in ('open_chat','open_info','open_members')]}


class GroupNavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.records=[observation(1,'A'),observation(2,'B')]

    def tearDown(self):self.tmp.cleanup()

    def backend(self,w,script,payload=None):
        record=next(r for r in self.records if r['window']['hwnd']==w['hwnd'])
        if script=='inspect_groups.ps1':return report(w,portuguese=record['account']=='B')
        self.assertEqual(script,'navigate_group.ps1')
        return opened(record,payload['slot'])

    def test_two_accounts_same_group_names_open_in_order_without_a_human_wait(self):
        calls=[];path=self.path/'open.json'
        def backend(w,script,payload=None):
            if script=='navigate_group.ps1':
                journal=json.loads(path.read_text());calls.append((w['hwnd'],payload['slot']))
                self.assertEqual(journal['jobs'][len(calls)-1]['state'],'opening')
                if len(calls)==2:self.assertEqual(journal['jobs'][0]['state'],'members_open')
            return self.backend(w,script,payload)
        with patch('group_navigation.run_window_script',side_effect=backend),patch('group_navigation.time.sleep'):
            result=open_pinned_groups(self.records,1,path)
        self.assertEqual(calls,[(101,1),(102,1)]);self.assertTrue(result['ok'])
        self.assertFalse(result['final_invite_clicked']);self.assertFalse(result['members_selected'])
        self.assertEqual([j['state'] for j in result['jobs']],['members_open','members_open'])

    def test_second_group_uses_second_runtime_id_and_does_not_allocate_numbers(self):
        with patch('group_navigation.run_window_script',side_effect=self.backend) as run,patch('group_navigation.time.sleep'):
            result=open_pinned_groups(self.records[:1],2,self.path/'second.json')
        self.assertTrue(result['ok']);payload=run.call_args.args[2]
        self.assertEqual(payload['targets'][1]['runtime_id'],self.records[0]['targets'][1]['runtime_id'])
        self.assertFalse(result['contact_database_updated'])

    def test_changed_name_order_or_list_identity_stops_before_navigation(self):
        for kind in ('name','order','list'):
            first=report();second=copy.deepcopy(first)
            if kind=='name':second['candidates'][0]['name']='Group, changed, Pinned'
            if kind=='order':second['candidates'][:2]=list(reversed(second['candidates'][:2]))
            if kind=='list':second['list_runtime_id']='new list'
            with patch('group_navigation.run_window_script',side_effect=[first,second]) as run,patch('group_navigation.time.sleep'):
                result=open_pinned_groups(self.records,1,self.path/(kind+'.json'))
            self.assertEqual(run.call_count,2);self.assertFalse(result['ok'])
            self.assertEqual([j['state'] for j in result['jobs']],['review','not_started'])

    def test_consistent_but_changed_since_saved_binding_requires_new_record(self):
        changed=report();changed['candidates'][0]['runtime_id']='new row'
        with patch('group_navigation.run_window_script',return_value=changed) as run,patch('group_navigation.time.sleep'):
            result=open_pinned_groups(self.records,1,self.path/'stale.json')
        self.assertEqual(run.call_count,2);self.assertFalse(result['ok'])

    def test_b_failure_preserves_a_open_page_and_stops_c(self):
        records=self.records+[observation(3,'C')];calls=[]
        failure={'ok':False,'stage':'initial','actions':[],'error':'An existing dialog is open.','final_invite_clicked':False}
        def backend(w,script,payload=None):
            calls.append((w['hwnd'],script))
            if w['hwnd']==102 and script=='navigate_group.ps1':raise WindowActionError('existing dialog',failure)
            return self.backend(w,script,payload)
        with patch('group_navigation.run_window_script',side_effect=backend),patch('group_navigation.time.sleep'):
            result=open_pinned_groups(records,1,self.path/'failure.json')
        self.assertFalse(result['ok']);self.assertFalse(any(w==103 for w,s in calls))
        self.assertEqual([j['state'] for j in result['jobs']],['members_open','review','not_started'])
        self.assertEqual(result['jobs'][1]['navigation'],failure)

    def test_incomplete_wrong_identity_or_extra_invite_action_never_advances_to_b(self):
        for kind in ('window','profile','dialog','selected','invited','database','action','name','errors'):
            bad=opened(self.records[0],1)
            if kind=='window':bad['window_handle']=999
            if kind=='profile':bad['profile_verified']=False
            if kind=='dialog':bad['dialog_runtime_id']=None
            if kind=='selected':bad['members_selected']=True
            if kind=='invited':bad['final_invite_clicked']=True
            if kind=='database':bad['contact_database_updated']=True
            if kind=='action':bad['actions'].append({'action':'final_add','attempted':True,'invoked':True})
            if kind=='name':bad['group_name']='other'
            if kind=='errors':bad['errors']=['unreadable']
            with patch('group_navigation.run_window_script',side_effect=[report(),report(),bad]) as run,patch('group_navigation.time.sleep'):
                result=open_pinned_groups(self.records,1,self.path/(kind+'.json'))
            self.assertEqual(run.call_count,3);self.assertFalse(result['ok'])

    def test_bad_windows_duplicate_accounts_and_missing_records_never_call_backend(self):
        variants=[[],[self.records[0],self.records[0]]]
        r=copy.deepcopy(self.records);r[1]['account']='A';variants.append(r)
        r=copy.deepcopy(self.records);r[1]['window']['pid']=True;variants.append(r)
        r=copy.deepcopy(self.records);r[1]['targets']=[];variants.append(r)
        with patch('group_navigation.run_window_script') as run:
            for records in variants:
                with self.assertRaises(ValueError):open_pinned_groups(records,1,self.path/'bad.json')
            for slot in (0,3,True):
                with self.assertRaises(ValueError):open_pinned_groups(self.records,slot,self.path/'bad.json')
        run.assert_not_called()

    def test_persisted_progress_distinguishes_slots_accounts_and_never_counts_as_invited(self):
        store=Store(self.path/'db.sqlite3');GroupCatalog(store);pins=PinnedGroupBindings(store)
        try:
            for slot in (1,2):
                with patch('group_navigation.run_window_script',side_effect=self.backend),patch('group_navigation.time.sleep'):
                    result=open_pinned_groups(self.records,slot,self.path/f'slot{slot}.json')
                pins.save_navigation(result)
            self.assertEqual(set(pins.navigation_states('A')),{1,2})
            self.assertEqual(pins.navigation_states('B')[1]['window']['hwnd'],102)
            self.assertEqual(pins.latest_navigation()['slot'],2)
            self.assertEqual(store.next_contact_number(),1)
            self.assertEqual(store.addition_stats()['total_added'],0)
        finally:store.close()

    def test_gui_freezes_window_list_order_and_uses_bound_account_not_name(self):
        from app import App
        app=App.__new__(App);app.group_operation_ready=MagicMock()
        app.windows=[window(1),window(2)];app.window_tree=MagicMock()
        app.window_tree.selection.return_value=('1','0');app.window_tree.get_children.return_value=('0','1')
        app.pinned_groups=MagicMock();app.pinned_groups.accounts.return_value=['A','B']
        app.pinned_groups.get.side_effect=lambda a:copy.deepcopy(next(r for r in self.records if r['account']==a))
        app.root=MagicMock();app.status=MagicMock();app.probe_button=MagicMock();app.probe_queue=queue.Queue()
        thread=lambda *,target,daemon:SimpleNamespace(start=target)
        with patch('app.DATA',self.path),patch('app.threading.Thread',side_effect=thread),\
                patch('app.open_pinned_groups',return_value={'ok':True}) as run:
            app.navigate_pinned_groups(2)
        self.assertEqual([r['account'] for r in run.call_args.args[0]],['A','B'])
        self.assertEqual(run.call_args.args[1],2)
        self.assertEqual(app.probe_queue.get_nowait(),(True,{'group_navigation_result':{'ok':True}}))


if __name__=='__main__':unittest.main()
