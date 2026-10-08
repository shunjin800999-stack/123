"""Group choice is independent of selection history; current evidence still binds."""
import copy
import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from app import App
from groups import GroupCatalog
from pinned_members import PinnedMemberPlans,select_pinned_member_queue
from store import Store
from test_group_navigation import observation,opened
from test_pinned_groups import report
from test_pinned_members import selected


class IndependentPinnedGroupsTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.path=Path(folder.name);self.store=Store(self.path/'test.sqlite3');self.addCleanup(self.store.close)
        GroupCatalog(self.store);self.plans=PinnedMemberPlans(self.store)
        self.records=[observation(1,'账号11'),observation(2,'账号12')]
        for record,numbers in zip(self.records,(['735','736'],['1000','1001'])):self.plans.save(record,numbers)
        self.trace=[]

    def native(self,window,script,payload=None):
        record=next(r for r in self.records if r['window']['hwnd']==window['hwnd'])
        if script=='inspect_groups.ps1':return report(window,portuguese=record['account']=='账号12')
        self.assertEqual(script,'navigate_group.ps1')
        self.trace.append(('open',record['account'],payload['slot']))
        return opened(record,payload['slot'])

    def select(self,window,path,numbers,progress=None,**options):
        record=next(r for r in self.records if r['window']['hwnd']==window['hwnd'])
        slot=int(options['expected_dialog_id'].rsplit(':',1)[1])
        self.assertTrue(options['fast_visible']);self.assertTrue(options['target_only'])
        self.trace.append(('select',record['account'],slot,numbers[:]))
        return selected(record,slot,numbers)

    def run_group(self,slot,*,plans=None):
        entries=self.plans.entries(self.records,slot) if plans is None else plans
        with patch('pinned_members.require_environment'),patch('group_navigation.time.sleep'),\
             patch('group_navigation.run_window_script',side_effect=self.native),\
             patch('pinned_members.select_members_test',side_effect=self.select):
            result=select_pinned_member_queue(entries,slot,self.path/f'run-{len(self.trace)}.json',
                fast_visible=True,target_only=True)
        self.assertTrue(result['ok'],result['reason']);self.assertFalse(result['final_invite_clicked'])
        self.assertFalse(result['contact_database_updated']);self.plans.save_run(result)
        return result

    def put_state(self,account,slot,job):
        with self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO pinned_member_states(account,slot,job_json) VALUES(?,?,?)',
                (account,slot,json.dumps(job)))

    def test_group2_starts_first_and_visits_each_accounts_actual_second_group(self):
        before=self.store.rows();result=self.run_group(2)
        self.assertEqual([j['slot'] for j in result['jobs']],[2,2])
        self.assertEqual([j['target'] for j in result['jobs']],[r['targets'][1] for r in self.records])
        self.assertEqual([j['numbers'] for j in result['jobs']],[['735','736'],['1000','1001']])
        self.assertEqual([t[:3] for t in self.trace],
            [('open','账号11',2),('select','账号11',2),('open','账号12',2),('select','账号12',2)])
        for record in self.records:self.assertEqual(list(self.plans.states(record['account'])),[2])
        self.assertEqual(self.store.rows(),before)

    def test_any_order_and_repeat_each_group_preserves_records(self):
        for slot in (2,1,2,1):
            before={r['account']:self.plans.states(r['account']).get(3-slot) for r in self.records}
            result=self.run_group(slot)
            self.assertEqual(result['slot'],slot)
            for record in self.records:
                self.assertEqual(self.plans.states(record['account']).get(3-slot),before[record['account']])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM pinned_member_runs').fetchone()[0],4)
        for record in self.records:
            states=self.plans.states(record['account'])
            self.assertEqual([states[s]['state'] for s in (1,2)],['selection_finished']*2)
        self.assertEqual(len([t for t in self.trace if t[0]=='select']),8)

    def test_other_group_missing_paused_review_or_stale_does_not_gate_execution(self):
        for state in ('paused','review','selection_finished','confirmed_invited'):
            with self.subTest(state=state):
                old={'account':'账号11','slot':1,'plan_id':'old-plan','numbers':['9'],'state':state}
                self.put_state('账号11',1,old)
                self.run_group(2)
                self.assertEqual(self.plans.states('账号11')[1],old)

    def test_same_group_old_invite_records_do_not_gate_either_group(self):
        for slot in (1,2):
            with self.subTest(slot=slot):
                plan=self.plans.get('账号11')
                old={'plan_id':plan['plan_id'],'numbers':plan['numbers'],'state':'confirmed_invited'}
                self.put_state('账号11',slot,old)
                entries=self.plans.entries(self.records,slot)
                self.assertEqual(entries[0]['plan_id'],plan['plan_id'])
                self.assertEqual(self.plans.states('账号11')[slot],old)
                self.run_group(slot)

    def test_new_list_after_old_group1_is_used_for_group2(self):
        self.run_group(1);old=copy.deepcopy(self.plans.states('账号11')[1])
        plan=self.plans.save(self.records[0],['1027','1028'])
        result=self.run_group(2)
        self.assertEqual(result['jobs'][0]['numbers'],['1027','1028'])
        self.assertEqual(result['jobs'][0]['plan_id'],plan['plan_id'])
        self.assertEqual(self.plans.states('账号11')[1],old)

    def test_detached_worker_does_not_require_or_validate_other_group_attachment(self):
        for legacy_attachment in (None,{'state':'paused','slot':1,'numbers':['9'],'plan_id':'wrong'}):
            with self.subTest(attachment=legacy_attachment):
                entries=[self.plans.get(r['account']) for r in self.records]
                if legacy_attachment is not None:entries[0]['group1_selection']=legacy_attachment
                before=copy.deepcopy(entries);self.run_group(2,plans=entries)
                self.assertEqual(entries,before)

    def test_reading_entries_preserves_history_and_addition_records(self):
        self.run_group(1)
        before=(self.plans.get('账号11'),self.plans.states('账号11'),self.store.rows(),self.store.addition_stats())
        for slot in (2,1):
            entry=self.plans.entries(self.records,slot)[0]
            self.assertEqual(entry,before[0]);self.assertNotIn('group1_selection',entry)
        self.assertEqual((self.plans.get('账号11'),self.plans.states('账号11'),self.store.rows(),self.store.addition_stats()),before)

    def test_invalid_current_binding_and_missing_plan_still_stop(self):
        for slot in (1,2):
            for field in ('pid','target'):
                with self.subTest(slot=slot,field=field):
                    changed=copy.deepcopy(self.records)
                    if field=='pid':changed[0]['window']['pid']+=1
                    else:changed[0]['targets'][slot-1]['name']='different group'
                    with self.assertRaises(ValueError):self.plans.entries(changed,slot)
            with self.assertRaises(ValueError):self.plans.entries([observation(3,'missing')],slot)
        self.assertEqual(self.trace,[])

    def test_gui_group2_starts_without_group1_and_keeps_chosen_start_account(self):
        app=App.__new__(App);app.group_operation_ready=Mock();app.group_account=SimpleNamespace(get=lambda:'账号11')
        app.selected_pinned_observations=Mock(return_value=self.records)
        app.pinned_member_plans=self.plans;app.store=self.store;app.member_pause=threading.Event()
        app.probe_button=Mock();app.status=Mock();app.root=Mock();app.probe_queue=queue.Queue()
        def immediate_thread(*,target,daemon):return SimpleNamespace(start=target)
        with patch('app.DATA',self.path),patch('backup_ocr.require_environment'),\
             patch('pinned_members.require_environment'),patch('group_cleanup.prepare_group_page',return_value={'ok':True}),\
             patch('group_navigation.time.sleep'),patch('group_navigation.run_window_script',side_effect=self.native),\
             patch('pinned_members.select_members_test',side_effect=self.select),\
             patch('app.threading.Thread',side_effect=immediate_thread):
            app.select_pinned_members(2)
        app.selected_pinned_observations.assert_called_once_with(require_account_picker=False,start_account='账号11')
        result=None
        while not app.probe_queue.empty():
            ok,value=app.probe_queue.get_nowait();self.assertTrue(ok,value)
            if 'pinned_members_result' in value:result=value['pinned_members_result']
        self.assertIsNotNone(result);self.assertTrue(result['ok'],result['reason'])
        self.assertEqual(result['slot'],2);self.assertEqual([j['account'] for j in result['jobs']],['账号11','账号12'])

    def freeze_batch(self):
        record=self.records[0]
        self.store.import_text('phone','+5516991234500\n+5516991234501')
        batch=self.store.create_batch(record['account'],2)
        for _ in range(2):
            item,_=self.store.reserve_next(batch);self.store.record_add_result(item['id'],'added')
        plan=self.plans.save_batch(record,batch)
        return batch,plan

    def test_confirmed_batch_group2_first_then_group1_finishes_without_invite_marks(self):
        batch,plan=self.freeze_batch();before=self.store.rows();stats=self.store.addition_stats()
        for slot in (2,1):
            self.run_group(slot)
            self.assertEqual(self.store.batch(batch)['status'],'active' if slot==2 else 'selection_done')
        self.assertEqual(self.store.rows(),before);self.assertEqual(self.store.addition_stats(),stats)
        for slot in (1,2):self.assertEqual(self.plans.entries(self.records,slot)[0]['numbers'],plan['numbers'])

    def test_legacy_completed_batch_can_repeat_without_rewriting_contacts(self):
        batch,plan=self.freeze_batch()
        with self.store.db:
            self.store.db.execute("UPDATE batches SET status='completed' WHERE id=?",(batch,))
            self.store.db.execute("UPDATE items SET status='completed' WHERE batch_id=?",(batch,))
        before=self.store.rows();stats=self.store.addition_stats()
        for slot in (2,1):
            self.put_state('账号11',slot,{'state':'confirmed_invited','plan_id':plan['plan_id'],'numbers':plan['numbers']})
            self.run_group(slot)
        self.assertEqual(self.store.rows(),before);self.assertEqual(self.store.addition_stats(),stats)
        self.assertEqual(self.store.batch(batch)['status'],'completed')

    def test_unconfirmed_or_changed_current_batch_members_still_stop(self):
        batch,plan=self.freeze_batch();item=plan['members'][0]['item_id']
        for change in ("status='uncertain'","contact_number=999"):
            with self.subTest(change=change),self.store.db:
                self.store.db.execute(f'UPDATE items SET {change} WHERE id=?',(item,))
                for slot in (1,2):
                    with self.assertRaises(ValueError):self.plans.entries(self.records,slot)
                self.store.db.execute("UPDATE items SET status='added',contact_number=1 WHERE id=?",(item,))
        self.assertEqual(self.trace,[])


if __name__=='__main__':unittest.main()
