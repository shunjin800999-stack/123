import copy
import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from groups import GroupCatalog
from pinned_members import PinnedMemberPlans, select_pinned_member_queue
from store import Store
from test_group_navigation import observation, opened
from test_pinned_groups import report


def selected(record,slot,numbers):
    w=record['window']
    return {'ok':True,'scope':'multi_member_selection','state':'waiting_for_manual_invite',
        'selection_verified':True,'numbers':numbers[:],'selected_numbers':numbers[:],
        'remaining_numbers':[],'final_invite_clicked':False,'database_updated':False,
        'final':{'ok':True,'read_only':True,'scope':'member_visual','window_handle':w['hwnd'],
            'process_id':w['pid'],'dialog_runtime_id':opened(record,slot)['dialog_runtime_id'],
            'final_invite_clicked':False}}


class PinnedMembersTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.store=Store(self.path/'db.sqlite3');GroupCatalog(self.store)
        self.plans=PinnedMemberPlans(self.store)
        self.records=[observation(1,'A'),observation(2,'B')]
        for record,numbers in zip(self.records,(['1','2'],['3','4'])):self.plans.save(record,numbers)
        env=patch('pinned_members.require_environment');env.start();self.addCleanup(env.stop)
        timer=patch('group_navigation.time.sleep');timer.start();self.addCleanup(timer.stop)

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def native(self,w,script,payload=None):
        record=next(r for r in self.records if r['window']['hwnd']==w['hwnd'])
        if script=='inspect_groups.ps1':return report(w,portuguese=record['account']=='B')
        self.assertEqual(script,'navigate_group.ps1')
        return opened(record,payload['slot'])

    def selection(self,w,path,numbers,progress=None,*,expected_dialog_id):
        record=next(r for r in self.records if r['window']['hwnd']==w['hwnd'])
        slot=int(expected_dialog_id.rsplit(':',1)[1])
        return selected(record,slot,numbers)

    def run_queue(self,slot=1,records=None,path=None):
        entries=self.plans.entries(records or self.records,slot)
        with patch('group_navigation.run_window_script',side_effect=self.native),\
                patch('pinned_members.select_members_test',side_effect=self.selection):
            return select_pinned_member_queue(entries,slot,path or self.path/f'run{slot}.json')

    def test_a_selected_and_durable_before_b_without_human_wait(self):
        path=self.path/'queue.json';events=[]
        def native(w,script,payload=None):
            if w['hwnd']==102:
                journal=json.loads(path.read_text())
                self.assertEqual(journal['jobs'][0]['state'],'waiting_for_manual_invite')
                self.assertEqual(journal['jobs'][0]['selection']['selected_numbers'],['1','2'])
            events.append((w['hwnd'],script))
            return self.native(w,script,payload)
        def choose(w,*args,**kwargs):
            events.append((w['hwnd'],'choose'))
            return self.selection(w,*args,**kwargs)
        with patch('group_navigation.run_window_script',side_effect=native),\
                patch('pinned_members.select_members_test',side_effect=choose):
            result=select_pinned_member_queue(self.plans.entries(self.records,1),1,path)
        self.assertTrue(result['ok'])
        self.assertEqual(events.index((101,'choose'))+1,events.index((102,'inspect_groups.ps1')))
        self.assertFalse(result['final_invite_clicked']);self.assertFalse(result['contact_database_updated'])

    def test_two_groups_use_same_frozen_list_per_account_after_restart(self):
        first=self.run_queue();self.plans.save_run(first)
        reopened=PinnedMemberPlans(self.store)
        self.assertEqual(reopened.entries(self.records,2)[0]['numbers'],['1','2'])
        second=self.run_queue(2);self.plans.save_run(second)
        for a,numbers in (('A',['1','2']),('B',['3','4'])):
            states=self.plans.states(a)
            self.assertEqual([states[s]['numbers'] for s in (1,2)],[numbers,numbers])
            self.assertEqual(states[1]['plan_id'],states[2]['plan_id'])
        self.assertEqual(self.store.next_contact_number(),1)
        self.assertEqual(self.store.addition_stats()['total_added'],0)
        self.assertEqual(self.plans.latest()['slot'],2)

    def test_group2_needs_current_plan_but_not_group1_result(self):
        self.assertEqual(self.plans.entries(self.records,2)[0]['numbers'],['1','2'])
        with self.assertRaises(ValueError):self.plans.entries([observation(3,'C')],1)

    def test_repeated_save_of_unchanged_list_preserves_group2_eligibility(self):
        self.plans.save_run(self.run_queue())
        plan_id=self.plans.get('A')['plan_id']
        self.plans.save(self.records[0],['1','2'])
        self.assertEqual(self.plans.entries(self.records,2)[0]['plan_id'],plan_id)

    def test_changed_binding_blocks_but_new_list_does_not_need_old_group1(self):
        self.plans.save_run(self.run_queue())
        for field in ('target','window'):
            rows=copy.deepcopy(self.records)
            if field=='target':rows[0]['targets'][0]['name']='changed'
            else:rows[0]['window']['pid']+=1
            with self.assertRaises(ValueError):self.plans.entries(rows,2)
        self.plans.save(self.records[0],['8','9'])
        self.assertEqual(self.plans.entries(self.records,2)[0]['numbers'],['8','9'])

    def test_live_changed_group_stops_before_search_or_selection(self):
        stale=report();stale['candidates'][0]['runtime_id']='replaced'
        with patch('group_navigation.run_window_script',return_value=stale),\
                patch('pinned_members.select_members_test') as select:
            result=select_pinned_member_queue(self.plans.entries(self.records,1),1,self.path/'changed.json')
        select.assert_not_called();self.assertFalse(result['ok'])
        self.assertEqual([j['state'] for j in result['jobs']],['review','not_started'])

    def test_nav_failure_or_incomplete_proof_never_enters_selection(self):
        for kind in ('failure','wrong_dialog','invited'):
            nav={'ok':True,'jobs':[{'navigation':opened(self.records[0],1)}]}
            if kind=='failure':nav['ok']=False
            if kind=='wrong_dialog':nav['jobs'][0]['navigation']['dialog_runtime_id']=None
            if kind=='invited':nav['jobs'][0]['navigation']['final_invite_clicked']=True
            with patch('pinned_members.open_pinned_groups',return_value=nav),\
                    patch('pinned_members.select_members_test') as select:
                result=select_pinned_member_queue(self.plans.entries(self.records,1),1,self.path/(kind+'.json'))
            select.assert_not_called();self.assertFalse(result['ok'])

    def test_wrong_final_dialog_missing_member_extra_member_or_invite_does_not_pass(self):
        for kind in ('dialog','missing','extra','invite'):
            value=selected(self.records[0],1,['1','2'])
            if kind=='dialog':value['final']['dialog_runtime_id']='other'
            if kind=='missing':value['selected_numbers']=['1']
            if kind=='extra':value['selected_numbers'].append('9')
            if kind=='invite':value['final_invite_clicked']=True
            with patch('group_navigation.run_window_script',side_effect=self.native) as native,\
                    patch('pinned_members.select_members_test',return_value=value):
                result=select_pinned_member_queue(self.plans.entries(self.records,1),1,self.path/(kind+'.json'))
            self.assertFalse(result['ok']);self.assertEqual(native.call_count,3)

    def test_b_selection_failure_preserves_a_and_leaves_c_unstarted(self):
        self.records.append(observation(3,'C'));self.plans.save(self.records[2],['5'])
        def choose(w,*args,**kwargs):
            if w['hwnd']==102:return {'ok':False,'reason':'未找到备注'}
            return self.selection(w,*args,**kwargs)
        with patch('group_navigation.run_window_script',side_effect=self.native) as native,\
                patch('pinned_members.select_members_test',side_effect=choose):
            result=select_pinned_member_queue(self.plans.entries(self.records,1),1,self.path/'failure.json')
        self.assertEqual([j['state'] for j in result['jobs']],['waiting_for_manual_invite','review','not_started'])
        self.plans.save_run(result)
        self.assertEqual(self.plans.states('A')[1]['state'],'waiting_for_manual_invite')
        self.assertEqual(self.plans.states('C'),{})
        self.assertFalse(any(c.args[0]['hwnd']==103 for c in native.call_args_list))

    def test_missing_ocr_environment_stops_before_any_navigation(self):
        with patch('pinned_members.require_environment',side_effect=ValueError('missing OCR')),\
                patch('pinned_members.open_pinned_groups') as native:
            result=select_pinned_member_queue(self.plans.entries(self.records,1),1,self.path/'env.json')
        native.assert_not_called();self.assertFalse(result['ok'])

    def test_unsaved_input_and_external_mutations_cannot_change_snapshot(self):
        labels=['1','2'];plan=self.plans.save(self.records[0],labels)
        labels.append('3');plan['numbers'].append('4')
        self.assertEqual(self.plans.get('A')['numbers'],['1','2'])
        with self.assertRaises(ValueError):self.plans.save(self.records[0],['1','001'])
        self.assertEqual(self.plans.get('A')['numbers'],['1','2'])

    def test_worker_uses_group2_target_without_group1_evidence(self):
        entries=self.plans.entries(self.records,1)
        with patch('group_navigation.run_window_script',side_effect=self.native),\
             patch('pinned_members.select_members_test',side_effect=self.selection):
            result=select_pinned_member_queue(entries,2,self.path/'group2.json')
        self.assertTrue(result['ok'],result['reason'])
        self.assertEqual([job['target'] for job in result['jobs']],[record['targets'][1] for record in self.records])
        self.assertEqual([job['slot'] for job in result['jobs']],[2,2])

    def test_gui_freezes_lists_on_main_thread_then_worker_uses_snapshot(self):
        from app import App
        app=App.__new__(App);app.member_pause=threading.Event();app.group_operation_ready=MagicMock()
        app.group_account=SimpleNamespace(get=lambda:'A')
        app.selected_pinned_observations=MagicMock(return_value=self.records)
        app.pinned_member_plans=self.plans
        app.store=self.store
        app.root=MagicMock();app.status=MagicMock();app.probe_button=MagicMock();app.probe_queue=queue.Queue()
        thread=lambda *,target,daemon:SimpleNamespace(start=target)
        with patch('app.DATA',self.path),patch('app.threading.Thread',side_effect=thread),\
                patch('backup_ocr.require_environment'),patch('app.select_pinned_member_queue',return_value={'ok':True}) as run:
            app.select_pinned_members(1)
        self.assertEqual([p['numbers'] for p in run.call_args.args[0]],[['1','2'],['3','4']])
        app.selected_pinned_observations.assert_called_once_with(require_account_picker=False,start_account='A')
        self.assertTrue(run.call_args.kwargs['prepare_pages'])
        self.assertTrue(run.call_args.kwargs['fast_visible'])
        self.assertEqual(run.call_args.kwargs['contacts_by_account'],{'A':[],'B':[]})
        self.assertEqual(app.probe_queue.get_nowait(),(True,{'pinned_members_result':{'ok':True}}))

    def test_pause_between_accounts_preserves_a_and_never_opens_b(self):
        event=threading.Event()
        def choose(w,*args,**kwargs):
            kwargs.pop('should_stop',None)
            result=self.selection(w,*args,**kwargs);event.set();return result
        with patch('group_navigation.run_window_script',side_effect=self.native) as native,\
             patch('pinned_members.select_members_test',side_effect=choose):
            r=select_pinned_member_queue(self.plans.entries(self.records,1),1,self.path/'pause.json',should_stop=event.is_set)
        self.assertEqual(r['state'],'paused');self.assertEqual(r['jobs'][0]['state'],'waiting_for_manual_invite')
        self.assertEqual(r['jobs'][1]['state'],'paused')
        self.assertTrue(all(c.args[0]['hwnd']==101 for c in native.call_args_list))
        self.plans.save_run(r);self.assertEqual(self.plans.states('B')[1]['state'],'paused')

    def test_pause_button_only_sets_request_for_active_selection(self):
        from app import App
        app=App.__new__(App);app.member_pause=threading.Event();app.status=MagicMock()
        app.member_selection_active=False;app.pause_member_selection();self.assertFalse(app.member_pause.is_set())
        app.member_selection_active=True;app.pause_member_selection();self.assertTrue(app.member_pause.is_set())


if __name__=='__main__':unittest.main()
