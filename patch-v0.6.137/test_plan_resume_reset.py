import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch

from app import App
from batch_plan_preparation import BatchPlanPreparation
from contact_queue import ContactQueue
from groups import GroupCatalog
from list_reset import reset_current_list
from pinned_groups import PinnedGroupBindings
from pinned_members import PinnedMemberPlans
from selection_scope import batch_allowed,current_queue
from store import Store
from test_contact_queue import profile,window
from test_pinned_groups import report


class ResumeResetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'progress.sqlite3'
        self.store=Store(self.path);self.catalog=GroupCatalog(self.store);self.bindings=PinnedGroupBindings(self.store);self.plans=PinnedMemberPlans(self.store)
        self.queue=ContactQueue(self.store,pinned_groups=self.bindings)
        self.store.import_text('phone','\n'.join(f'+55169912345{i:02d}' for i in range(40)))
        for i in (1,2,3):
            read=report(window(i));self.bindings.save(f'账号{i}',window(i),read,copy.deepcopy(read))
        self.qid=self.queue.configure([{'window':window(i),'account':f'账号{i}','target':2} for i in (1,2,3)],
            new_batch=True,independent=True,replace_selection=True)

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def fill(self):
        job=self.queue.current(self.qid);job=self.queue.claim_fill(self.qid,job['id'])
        self.queue.finish_fill(self.qid,job['id'],{'ok':True,'mode':'fill_only','number':str(job['preview_number']),
            'phone_matches':True,'empty_guard':True,'contact_created':False,'final_invite_clicked':False})
        return self.queue.current(self.qid)

    def add(self):
        job=self.fill();read=profile(job)
        self.queue.accept(self.qid,job['id'],[read,copy.deepcopy(read)])
        return job

    def stopped_at_account2(self):
        self.queue.start(self.qid)
        for _ in range(3):self.add()
        failed=self.fill();self.queue.stop(self.qid,'模拟网络导致未确认',review=True)
        return failed

    def test_continue_partial_account_preserves_successes_and_retires_number(self):
        old=self.stopped_at_account2();successes=[r for r in self.store.rows() if r['added_at']]
        result=self.queue.prepare_from_account(self.qid,'账号2')
        self.assertEqual(result['voided_numbers'],[4]);self.assertEqual(result['voided_item_ids'],[old['item_id']])
        self.assertTrue(Path(result['backup_path']).is_file())
        self.assertEqual(successes,[r for r in self.store.rows() if r['added_at']])
        self.assertEqual(self.store.next_contact_number(),5);self.assertEqual(current_queue(self.store.db),self.qid)
        job=self.queue.start(self.qid);self.assertEqual(job['account'],'账号2');self.assertNotEqual(job['item_id'],old['item_id'])
        for _ in range(3):self.add()
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'done')
        self.assertEqual([j['added_numbers'] for j in self.queue.snapshot(self.qid)['jobs']],[[1,2],[3,5],[6,7]])
        self.assertEqual(next(r for r in self.store.rows() if r['id']==old['item_id'])['status'],'void')

    def test_start_later_retains_partial_earlier_account_in_invitation_lists(self):
        self.stopped_at_account2();self.queue.prepare_from_account(self.qid,'账号3');self.queue.start(self.qid)
        self.add();self.add()
        snapshot=self.queue.snapshot(self.qid)
        self.assertEqual(snapshot['jobs'][1]['state'],'skipped_account')
        bulk=BatchPlanPreparation(self.store,self.plans,self.bindings,self.queue)
        preview=bulk.preview(self.qid);saved=bulk.confirm(preview)
        self.assertEqual([r['numbers'] for r in saved['saved']],[['1','2'],['3'],['5','6']])

    def test_new_plan_can_start_at_third_and_return_to_previous_accounts_later(self):
        self.queue.prepare_from_account(self.qid,'账号3');self.queue.start(self.qid);self.add();self.add()
        self.assertEqual([j['added_numbers'] for j in self.queue.snapshot(self.qid)['jobs']],[[],[],[1,2]])
        self.queue.prepare_from_account(self.qid,'账号1');self.queue.start(self.qid)
        for _ in range(4):self.add()
        self.assertEqual([j['added_numbers'] for j in self.queue.snapshot(self.qid)['jobs']],[[3,4],[5,6],[1,2]])

    def test_unfilled_failure_does_not_discard_an_unwritten_number(self):
        self.queue.start(self.qid);old=self.queue.current(self.qid);self.queue.stop(self.qid,review=True)
        result=self.queue.prepare_from_account(self.qid,'账号1')
        self.assertEqual(result['voided_item_ids'],[old['item_id']]);self.assertEqual(result['voided_numbers'],[])
        self.assertEqual(self.store.next_contact_number(),1)

    def test_old_callback_cannot_confirm_retired_task(self):
        old=self.stopped_at_account2();read=profile(old)
        self.queue.prepare_from_account(self.qid,'账号2');self.queue.start(self.qid)
        with self.assertRaises(ValueError):self.queue.accept(self.qid,old['id'],[read,read])
        self.assertEqual(self.store.next_contact_number(),5)
        self.assertEqual(sum(bool(r['added_at']) for r in self.store.rows()),3)

    def test_resume_wrong_account_running_and_old_plan_are_rejected(self):
        with self.assertRaises(ValueError):self.queue.prepare_from_account(self.qid,'账号99')
        self.queue.start(self.qid)
        with self.assertRaises(ValueError):self.queue.prepare_from_account(self.qid,'账号1')
        self.queue.stop(self.qid,review=True)
        newer=self.queue.configure([{'window':window(4),'account':'账号4','target':1}],new_batch=True,independent=True,replace_selection=True)
        with self.assertRaises(ValueError):self.queue.prepare_from_account(self.qid,'账号1')
        self.assertEqual(self.queue.latest(),newer)

    def test_resume_rejects_frozen_partial_list_without_retiring_held_item(self):
        old=self.stopped_at_account2();batch=self.queue.snapshot(self.qid)['jobs'][1]['batch_id']
        self.store.db.execute('INSERT INTO pinned_batch_member_plans(batch_id,account,plan_json) VALUES(?,?,?)',(batch,'账号2','{}'));self.store.db.commit()
        with self.assertRaises(ValueError):self.queue.prepare_from_account(self.qid,'账号2')
        self.assertEqual(self.store.next_contact_number(),4)
        self.assertEqual(next(r for r in self.store.rows() if r['id']==old['item_id'])['status'],'reserved')

    def test_retired_number_survives_database_reopen(self):
        self.stopped_at_account2();self.queue.prepare_from_account(self.qid,'账号2')
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.assertEqual(self.store.next_contact_number(),5)
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'configured')

    def test_clear_list_keeps_database_rows_events_successes_and_blocks_old_plans(self):
        self.queue.start(self.qid);self.add();old_batch=self.queue.snapshot(self.qid)['jobs'][0]['batch_id']
        self.queue.stop(self.qid);before=self.store.rows();events=self.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        result=reset_current_list(self.queue)
        self.assertEqual(self.store.rows(),[]);self.assertEqual(self.store.batches(),[])
        self.assertEqual(len(self.store.rows(include_hidden=True)),len(before))
        old_success=next(r for r in before if r['added_at'])
        saved=next(r for r in self.store.rows(include_hidden=True) if r['id']==old_success['id'])
        for field in ('value','account','contact_number','added_at','status','batch_id'):
            self.assertEqual(saved[field],old_success[field])
        self.assertGreater(self.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],events)
        self.assertEqual(self.store.next_contact_number(),0);self.assertIsNone(self.queue.latest())
        self.assertFalse(batch_allowed(self.store.db,old_batch));self.assertTrue(Path(result['backup_path']).is_file())
        with self.assertRaises(ValueError):self.plans.batch_members(old_batch)
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_new_round_starts_at_zero_then_one_even_after_restart(self):
        self.queue.start(self.qid);self.add();self.queue.stop(self.qid);reset_current_list(self.queue)
        self.store.import_text('phone','+5516989999901\n+5516989999902\n+5516989999903')
        self.qid=self.queue.configure([{'window':window(1),'account':'账号1','target':2}],new_batch=True,independent=True,replace_selection=True)
        self.queue.start(self.qid);self.assertEqual(self.add()['preview_number'],0)
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.assertEqual(self.store.next_contact_number(),1)
        self.queue.prepare_from_account(self.qid,'账号1');self.queue.start(self.qid)
        self.assertEqual(self.add()['preview_number'],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM items WHERE contact_number=1').fetchone()[0],2)
        self.assertEqual(self.store.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_second_reset_retains_two_old_zeros_and_no_old_ready_entry_is_claimed(self):
        for value in ('+5516989999901','+5516989999902'):
            reset_current_list(self.queue);self.store.import_text('phone',value)
            batch=self.store.create_batch('账号1',1);row,_=self.store.reserve_next(batch)
            self.assertEqual(row['value'],value);self.assertEqual(self.store.record_add_result(row['id'],'added'),0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM items WHERE contact_number=0').fetchone()[0],2)
        self.assertEqual(self.store.next_contact_number(),1)

    def test_reset_preserves_dedup_and_can_reintroduce_unused_input(self):
        self.queue.start(self.qid);old_success=self.add();self.queue.stop(self.qid)
        ready=next(r for r in self.store.rows() if r['status']=='ready')
        reset_current_list(self.queue)
        result=self.store.import_text('phone',old_success['item']['value']+'\n'+ready['value'])
        self.assertEqual(result['duplicate'],2);self.assertEqual(result['new'],0)
        self.assertEqual([r['id'] for r in self.store.rows()],[ready['id']])
        self.assertEqual(self.store.rows()[0]['number_epoch'],1)

    def test_empty_reset_reopens_at_zero_and_backup_retains_original_list(self):
        before=self.store.rows();result=reset_current_list(self.queue)
        with sqlite3.connect(result['backup_path']) as backup:
            self.assertEqual(backup.execute('SELECT COUNT(*) FROM items').fetchone()[0],len(before))
            self.assertEqual(backup.execute('SELECT next_value FROM number_sequence').fetchone()[0],1)
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.assertEqual(self.store.next_contact_number(),0);self.assertEqual(self.store.rows(),[])

    def test_resuming_does_not_rewind_numbers_used_by_other_account(self):
        self.stopped_at_account2()
        batch=self.store.create_batch('extra',1);item,_=self.store.reserve_next(batch)
        self.store.record_add_result(item['id'],'added')
        self.assertEqual(self.store.next_contact_number(),5)
        self.queue.prepare_from_account(self.qid,'账号2');self.assertEqual(self.store.next_contact_number(),5)

    def test_running_reset_is_rejected_without_mutations(self):
        self.queue.start(self.qid);before=self.store.rows()
        with self.assertRaises(ValueError):reset_current_list(self.queue)
        self.assertEqual(self.store.rows(),before);self.assertEqual(self.store.next_contact_number(),1)

    def test_resuming_from_zero_retires_zero_and_displays_it_as_zero(self):
        reset_current_list(self.queue);self.store.import_text('phone','+5516989999901\n+5516989999902')
        self.qid=self.queue.configure([{'window':window(1),'account':'账号1','target':1}],new_batch=True,independent=True,replace_selection=True)
        self.queue.start(self.qid);old=self.fill()
        app=App.__new__(App);app.adding_tree=Mock();app.adding_tree.get_children.return_value=();app.adding_summary=Mock();app.adding_queue=self.queue;app.store=self.store;app.probing=False
        app.refresh_adding_queue();values=app.adding_tree.insert.call_args.kwargs['values']
        self.assertEqual(values[-1],0)
        self.queue.stop(self.qid,review=True);result=self.queue.prepare_from_account(self.qid,'账号1')
        self.assertEqual(result['voided_numbers'],[0]);self.assertEqual(self.store.next_contact_number(),1)

    def test_old_v135_database_migrates_with_queue_and_foreign_keys_intact(self):
        legacy=Path(self.temp.name)/'v135.sqlite3'
        with sqlite3.connect(legacy) as db:
            db.executescript('''
              CREATE TABLE batches(id INTEGER PRIMARY KEY,account TEXT NOT NULL,target INTEGER NOT NULL,mode TEXT DEFAULT 'phone',status TEXT DEFAULT 'active',next_number INTEGER NOT NULL,reason TEXT DEFAULT '',created TEXT DEFAULT CURRENT_TIMESTAMP);
              CREATE TABLE items(id INTEGER PRIMARY KEY,source TEXT NOT NULL,seq INTEGER NOT NULL,value TEXT NOT NULL,origin TEXT NOT NULL,origin_line INTEGER NOT NULL,status TEXT DEFAULT 'ready',batch_id INTEGER REFERENCES batches(id),account TEXT,contact_number INTEGER,reason TEXT DEFAULT '',updated TEXT DEFAULT CURRENT_TIMESTAMP,numbering_global INTEGER NOT NULL DEFAULT 0,added_at TEXT,UNIQUE(source,value),UNIQUE(source,seq));
              CREATE UNIQUE INDEX global_contact_number ON items(contact_number) WHERE numbering_global=1 AND contact_number IS NOT NULL;
              CREATE TABLE number_sequence(id INTEGER PRIMARY KEY CHECK(id=1),next_value INTEGER NOT NULL CHECK(next_value>=1));
              CREATE TABLE events(id INTEGER PRIMARY KEY,time TEXT DEFAULT CURRENT_TIMESTAMP,action TEXT,item_id INTEGER,batch_id INTEGER,detail TEXT);
              CREATE TABLE contact_queues(id INTEGER PRIMARY KEY,state TEXT NOT NULL,reason TEXT DEFAULT '',created TEXT DEFAULT CURRENT_TIMESTAMP,target_mode TEXT DEFAULT 'contact',replace_selection INTEGER DEFAULT 1);
              CREATE TABLE contact_queue_jobs(id INTEGER PRIMARY KEY,queue_id INTEGER NOT NULL REFERENCES contact_queues(id),position INTEGER NOT NULL,batch_id INTEGER NOT NULL REFERENCES batches(id),window_json TEXT NOT NULL,state TEXT DEFAULT 'queued',item_id INTEGER REFERENCES items(id),preview_number INTEGER,reason TEXT DEFAULT '',UNIQUE(queue_id,position));
              INSERT INTO batches(id,account,target,next_number) VALUES(1,'old',1,2),(2,'账号1',1,2);
              INSERT INTO items(id,source,seq,value,origin,origin_line,status,batch_id,account,contact_number,numbering_global,added_at) VALUES(1,'phone',1,'+5516995555501','fixture',1,'added',1,'old',1,1,'2026-10-09 00:00:00'),(2,'phone',2,'+5516995555502','fixture',2,'ready',NULL,NULL,NULL,0,NULL);
              INSERT INTO number_sequence VALUES(1,2);
              INSERT INTO contact_queues(id,state) VALUES(1,'configured');
            ''')
            db.execute('INSERT INTO contact_queue_jobs(queue_id,position,batch_id,window_json) VALUES(1,1,2,?)',(json.dumps(window(1)),))
        store=Store(legacy)
        try:
            queue=ContactQueue(store);self.assertEqual(store.next_contact_number(),2)
            self.assertEqual(len(store.rows()),2);self.assertEqual(queue.snapshot()['jobs'][0]['account'],'账号1')
            self.assertEqual(store.db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertTrue(legacy.with_name('v135-before-v136-number-epochs.sqlite3').exists())
            reset_current_list(queue);self.assertEqual(store.next_contact_number(),0)
        finally:store.close()

    def fake_app(self):
        import queue
        app=App.__new__(App);app.root=Mock();app.probing=False;app.member_selection_active=False
        app.adding_queue=self.queue;app.store=self.store;app.pinned_groups=self.bindings;app.pinned_member_plans=self.plans
        app.adding_start_account=Mock();app.adding_start_account.get.return_value='账号3'
        app.task_notifier=Mock();app.status=Mock();app.probe_button=Mock();app.probe_queue=queue.Queue()
        app.refresh=Mock();app.refresh_groups=Mock();app.load_pinned_member_input=Mock();app.show_text=Mock()
        return app

    def test_ui_resume_only_prepares_selected_and_later_windows_then_starts_original_plan(self):
        self.stopped_at_account2();self.store.db.execute('UPDATE contact_queues SET auto_submit=1,auto_close_profile=1 WHERE id=?',(self.qid,));self.store.db.commit()
        app=self.fake_app()
        class InlineThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        with tempfile.TemporaryDirectory() as data,patch('app.DATA',Path(data)),patch('app.threading.Thread',InlineThread),\
             patch('app.prepare_pinned_scan',return_value={'ok':True}) as prepare,patch('app.clean_group_pages') as old_cleanup:
            app.start_adding_queue()
            self.assertEqual([c.args[0]['hwnd'] for c in prepare.call_args_list],[window(3)['hwnd']]);old_cleanup.assert_not_called()
            while not app.probe_queue.empty():app.poll_probe()
        self.assertEqual(self.queue.current(self.qid)['account'],'账号3');self.assertEqual(self.store.next_contact_number(),5)
        self.assertEqual(self.queue.current(self.qid)['queue_id'],self.qid)
        self.assertEqual(self.queue.snapshot(self.qid)['jobs'][1]['added_numbers'],[3])

    def test_ui_preparation_failure_alerts_and_does_not_submit(self):
        self.stopped_at_account2();app=self.fake_app();app.probing=True
        app.probe_queue.put((True,{'adding_cleanup_result':{'ok':False},'queue_id':self.qid}))
        app.poll_probe();app.task_notifier.finish_addition.assert_called_once_with(self.qid,False)
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'review')

    def test_ui_member_outcomes_alert_before_result_and_manual_pause_is_silent(self):
        for state,ok,expected in (('review',False,'stopped'),('done',True,'finished'),('paused',False,None)):
            app=self.fake_app();app.probing=True;app.member_selection_active=True
            app.pinned_member_plans=Mock();app.pinned_member_plans.save_run.return_value=[]
            app.probe_queue.put((True,{'pinned_members_result':{'state':state,'ok':ok,'reason':'fixture','jobs':[]}}))
            app.poll_probe()
            if expected:app.task_notifier.emit.assert_called_once_with(expected)
            else:app.task_notifier.emit.assert_not_called()

    def test_ui_unexpected_selection_worker_error_alerts(self):
        app=self.fake_app();app.probing=True;app.member_selection_active=True;app.probe_queue.put((False,'fixture failed'))
        with patch('app.messagebox.showwarning'):app.poll_probe()
        app.task_notifier.emit.assert_called_once_with('stopped')

    def test_ui_manual_addition_stop_disarms_audio(self):
        app=self.fake_app();app.refresh_adding_queue=Mock();app.stop_adding_queue()
        app.task_notifier.disarm_addition.assert_called_once_with(self.qid)
