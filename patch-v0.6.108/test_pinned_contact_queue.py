import copy
import json
import tempfile
import unittest
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from contact_queue import ContactQueue
from groups import GroupCatalog
from pinned_groups import PinnedGroupBindings
from pinned_members import PinnedMemberPlans
from store import Store
from test_contact_queue import window, profile, limit
from test_pinned_groups import report


class PinnedContactQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'db.sqlite3'
        self.store=Store(self.path)
        self.catalog=GroupCatalog(self.store)
        self.bindings=PinnedGroupBindings(self.store)
        self.plans=PinnedMemberPlans(self.store)
        self.queue=ContactQueue(self.store,group_catalog=self.catalog,pinned_groups=self.bindings)
        for i,account in enumerate(('A','B'),1):
            r=report(window(i),portuguese=i==2)
            self.bindings.save(account,window(i),r,copy.deepcopy(r))
        self.store.import_text('phone','\n'.join(f'+55169912345{i:02d}' for i in range(10)))
        self.store.import_text('username','@test_example_a')
        self.store.set_next_number(7)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def entries(self,targets=(2,1)):
        return [{'account':chr(64+i),'window':window(i),'target':t} for i,t in enumerate(targets,1)]

    def fill(self,qid):
        job=self.queue.current(qid)
        claimed=self.queue.claim_fill(qid,job['id'])
        self.queue.finish_fill(qid,claimed['id'],{'ok':True,'mode':'fill_only',
            'number':str(claimed['preview_number']),'phone_matches':True,'empty_guard':True,
            'contact_created':False,'final_invite_clicked':False})
        return self.queue.current(qid)

    def added(self,qid):
        job=self.fill(qid)
        r=profile(job)
        if job['account']=='B':
            r['controls'][2]['name']='Editar contato'
            r['controls'][3]['name']='Apagar contato'
        return self.queue.accept(qid,job['id'],[r,copy.deepcopy(r)])

    def test_new_batch_preserves_frozen_plans_and_numbers_after_reopen(self):
        qid=self.queue.configure(self.entries((1,1)));self.queue.start(qid)
        self.added(qid);self.added(qid)
        old_jobs=self.queue.snapshot(qid)['jobs']
        old_plans=[self.plans.save_batch(self.bindings.get(j['account']),j['batch_id']) for j in old_jobs]
        old_items=[dict(r) for r in self.store.db.execute('SELECT * FROM items WHERE contact_number IS NOT NULL')]
        nxt=self.store.next_contact_number()
        new_id=self.queue.configure(self.entries((5,5)),new_batch=True)
        jobs=self.queue.snapshot(new_id)['jobs']
        self.assertEqual([j['target'] for j in jobs],[5,5])
        self.assertEqual([j['added_numbers'] for j in jobs],[[],[]])
        for old,j,plan in zip(old_jobs,jobs,old_plans):
            self.assertNotEqual(old['batch_id'],j['batch_id'])
            self.assertEqual(self.store.batch(old['batch_id'])['status'],'waiting')
            self.assertEqual(self.plans.get(old['account']),plan)
        self.assertEqual([dict(r) for r in self.store.db.execute('SELECT * FROM items WHERE contact_number IS NOT NULL')],old_items)
        self.assertEqual(self.store.next_contact_number(),nxt)
        self.store.close();self.store=Store(self.path)
        self.queue=ContactQueue(self.store,pinned_groups=PinnedGroupBindings(self.store))
        self.queue.start(new_id)
        job=self.queue.current(new_id)
        self.assertEqual(job['account'],'A')
        self.assertEqual(self.queue.claim_fill(new_id,job['id'])['preview_number'],nxt)

    def test_new_batch_waiting_history_and_unknown_other_account_rollback(self):
        qid=self.queue.configure(self.entries((1,1)));self.queue.start(qid)
        self.added(qid);self.added(qid)
        old_jobs=self.queue.snapshot(qid)['jobs']
        for j in old_jobs:self.store.mark_waiting(j['batch_id'])
        new_id=self.queue.configure(self.entries((5,5)),new_batch=True)
        self.queue.start(new_id)
        self.assertEqual(self.queue.current(new_id)['account'],'A')
        self.queue.stop(new_id)
        before=[tuple(r) for r in self.store.db.execute('SELECT * FROM batches')]
        with self.assertRaises(ValueError):self.queue.configure(self.entries((5,5)),new_batch=True)
        self.assertEqual([tuple(r) for r in self.store.db.execute('SELECT * FROM batches')],before)

    def test_addition_cleanup_includes_successful_new_batch_without_changing_old_plan(self):
        from group_cleanup import saved_addition_contacts
        qid=self.queue.configure(self.entries((1,1)));self.queue.start(qid)
        self.added(qid);self.added(qid)
        old=self.queue.snapshot(qid)['jobs'][0]
        record=self.bindings.get('A');plan=self.plans.save_batch(record,old['batch_id'])
        qid2=self.queue.configure(self.entries((1,1)),new_batch=True);self.queue.start(qid2)
        self.added(qid2)
        contacts=saved_addition_contacts(self.store,record,plan)
        self.assertEqual([c['number'] for c in contacts],['7','9'])
        self.assertEqual(self.plans.get('A'),plan)
        wrong=copy.deepcopy(record);wrong['window']['pid']+=1
        self.assertEqual(saved_addition_contacts(self.store,wrong),[])
        self.assertEqual([c['number'] for c in saved_addition_contacts(self.store,self.bindings.get('B'))],['8'])
        with self.store.db:self.store.db.execute("UPDATE items SET status='uncertain' WHERE contact_number=9")
        self.assertEqual([c['number'] for c in saved_addition_contacts(self.store,record,plan)],['7'])

    def test_new_batch_adds_exactly_five_per_account_then_stops(self):
        self.store.import_text('phone','+5516999876501\n+5516999876502')
        qid=self.queue.configure(self.entries((1,1)));self.queue.start(qid)
        self.added(qid);self.added(qid)
        new_id=self.queue.configure(self.entries((5,5)),new_batch=True)
        self.queue.start(new_id)
        for i in range(10):
            self.assertEqual(self.queue.current(new_id)['account'],'A' if i<5 else 'B')
            self.added(new_id)
        snap=self.queue.snapshot(new_id)
        self.assertEqual(snap['state'],'done')
        self.assertEqual([j['added_numbers'] for j in snap['jobs']],[list(range(9,14)),list(range(14,19))])
        self.assertEqual(self.store.next_contact_number(),19)
        self.assertIsNone(self.queue.current(new_id))
        self.assertEqual([j['added_numbers'] for j in self.queue.snapshot(qid)['jobs']],[[7],[8]])

    def test_new_batch_b_failure_rolls_back_a_archive(self):
        qid=self.queue.configure(self.entries((1,1)));self.queue.start(qid)
        self.added(qid);self.added(qid)
        jobs=self.queue.snapshot(qid)['jobs']
        with self.store.db:self.store.db.execute("UPDATE batches SET status='review' WHERE id=?",(jobs[1]['batch_id'],))
        with self.assertRaises(ValueError):self.queue.configure(self.entries((5,5)),new_batch=True)
        self.assertEqual(self.store.batch(jobs[0]['batch_id'])['status'],'active')
        self.assertEqual(len(self.store.batches()),2)

    def change_binding(self,account):
        row=self.store.db.execute('SELECT observation_json FROM pinned_group_observations WHERE account=?',(account,)).fetchone()
        r=json.loads(row[0]);r['targets'][0]['name']='Changed target'
        self.store.db.execute('UPDATE pinned_group_observations SET observation_json=? WHERE account=?',(json.dumps(r),account))
        self.store.db.commit()

    def test_existing_pins_enable_queue_without_catalog_and_success_numbers_freeze_per_account(self):
        qid=self.queue.configure(self.entries())
        snap=self.queue.snapshot(qid)
        self.assertTrue(snap['phone_only'])
        self.assertEqual(snap['target_mode'],'pinned')
        self.assertEqual(len(snap['jobs'][0]['target_groups']),2)
        self.assertIsNone(self.catalog.catalog('A'))
        self.queue.start(qid)
        for _ in range(3):self.assertEqual(self.added(qid)['outcome'],'added')
        snap=self.queue.snapshot(qid)
        self.assertEqual(snap['state'],'done')
        self.assertEqual([j['added_numbers'] for j in snap['jobs']],[[7,8],[9]])
        self.assertEqual(snap['next_number'],10)
        self.assertEqual(self.store.addition_stats()['total_added'],3)
        before=self.store.rows()
        for job,numbers in zip(snap['jobs'],(['7','8'],['9'])):
            plan=self.plans.save_batch(self.bindings.get(job['account']),job['batch_id'])
            self.assertEqual(plan['numbers'],numbers)
        self.assertEqual(self.store.rows(),before)
        self.assertEqual(self.store.next_contact_number(),10)
        self.assertFalse(snap['final_invite_clicked'])
        self.assertEqual(next(r for r in self.store.rows() if r['source']=='username')['status'],'ready')

    def test_one_window_supported_and_reversed_order_does_not_rename_accounts(self):
        one=self.queue.configure(self.entries((1,)))
        self.assertEqual(len(self.queue.snapshot(one)['jobs']),1)
        entries=list(reversed(self.entries()))
        qid=self.queue.configure(entries)
        self.assertEqual([j['account'] for j in self.queue.snapshot(qid)['jobs']],['B','A'])
        self.queue.start(qid)
        for _ in range(3):self.added(qid)
        self.assertEqual([j['added_numbers'] for j in self.queue.snapshot(qid)['jobs']],[[7],[8,9]])

    def test_missing_or_wrong_bound_account_rolls_back_all_batches(self):
        for key,value in (('account','Unknown'),('window',window(1))):
            entries=self.entries();entries[1][key]=value
            with self.assertRaises(ValueError):self.queue.configure(entries)
            self.assertEqual(self.store.batches(),[])
            self.assertIsNone(self.queue.latest())
        self.assertTrue(all(r['status']=='ready' for r in self.store.rows()))

    def test_changed_target_before_start_prevents_any_reservation(self):
        qid=self.queue.configure(self.entries())
        self.change_binding('B')
        with self.assertRaisesRegex(ValueError,'已变化'):self.queue.start(qid)
        self.assertEqual(self.queue.snapshot(qid)['state'],'configured')
        self.assertTrue(all(r['status']=='ready' for r in self.store.rows()))
        self.assertEqual(self.store.next_contact_number(),7)

    def test_changed_target_after_fill_does_not_register_or_advance(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        job=self.fill(qid);r=profile(job)
        self.change_binding('A')
        with self.assertRaisesRegex(ValueError,'已变化'):self.queue.accept(qid,job['id'],[r,r])
        self.assertEqual(self.store.next_contact_number(),7)
        self.assertEqual(self.queue.current(qid)['item']['status'],'reserved')
        self.assertEqual(self.queue.snapshot(qid)['jobs'][1]['state'],'queued')

    def test_changed_B_after_A_success_stops_before_reserving_B(self):
        qid=self.queue.configure(self.entries((1,1)));self.queue.start(qid)
        job=self.fill(qid);r=profile(job)
        self.change_binding('B')
        self.assertEqual(self.queue.accept(qid,job['id'],[r,r])['outcome'],'added')
        snap=self.queue.snapshot(qid)
        self.assertEqual(snap['state'],'review')
        self.assertEqual(snap['jobs'][0]['added_numbers'],[7])
        self.assertEqual(snap['jobs'][1]['item_id'],None)
        self.assertEqual(self.store.next_contact_number(),8)

    def test_phone_exhaustion_never_claims_username_or_changes_source_mode(self):
        self.store.db.execute("DELETE FROM items WHERE source='phone' AND seq>2");self.store.db.commit()
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        self.added(qid);self.added(qid)
        snap=self.queue.snapshot(qid)
        self.assertEqual(snap['state'],'done')
        self.assertEqual(snap['jobs'][1]['state'],'no_list')
        self.assertEqual(snap['jobs'][1]['batch_status'],'active')
        self.assertEqual(self.store.batch(snap['jobs'][1]['batch_id'])['mode'],'phone')
        row=next(r for r in self.store.rows() if r['source']=='username')
        self.assertEqual(row['status'],'ready');self.assertIsNone(row['batch_id'])
        self.assertEqual(self.store.next_contact_number(),9)

    def test_restart_preserves_bindings_and_held_user_without_auto_resume(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        job=self.fill(qid);before=self.queue.snapshot(qid)
        self.store.close();self.store=Store(self.path)
        self.bindings=PinnedGroupBindings(self.store)
        self.queue=ContactQueue(self.store,pinned_groups=self.bindings)
        after=self.queue.snapshot(qid)
        self.assertEqual(after['state'],'review')
        self.assertEqual(after['jobs'][0]['pinned_binding'],before['jobs'][0]['pinned_binding'])
        self.assertEqual(after['jobs'][0]['item_id'],job['item_id'])
        self.assertEqual(after['jobs'][0]['item']['status'],'reserved')
        self.assertEqual(self.store.next_contact_number(),7)
        with self.assertRaises(ValueError):self.queue.start(qid)

    def test_frozen_batch_cannot_be_reconfigured_or_record_another_success(self):
        qid=self.queue.configure(self.entries((1,1)));self.queue.start(qid)
        self.added(qid)
        a=self.queue.snapshot(qid)['jobs'][0]
        self.plans.save_batch(self.bindings.get('A'),a['batch_id'])
        self.queue.stop(qid)
        with self.assertRaisesRegex(ValueError,'已生成两群名单'):self.queue.configure(self.entries())
        self.assertEqual(self.queue.latest(),qid)
        self.assertEqual(self.store.next_contact_number(),8)

    def test_binding_change_during_fill_does_not_accept_fill_result(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        job=self.queue.current(qid);claimed=self.queue.claim_fill(qid,job['id'])
        self.change_binding('A')
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.queue.finish_fill(qid,claimed['id'],{'ok':True,'mode':'fill_only','number':'7',
                'phone_matches':True,'empty_guard':True,'contact_created':False,'final_invite_clicked':False})
        self.assertEqual(self.store.next_contact_number(),7)
        self.assertEqual(self.queue.current(qid)['state'],'filling')
        self.assertEqual(self.queue.current(qid)['item']['status'],'reserved')

    def test_queue_target_cannot_be_replaced_when_generating_two_group_plan(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        for _ in range(3):self.added(qid)
        a=self.queue.snapshot(qid)['jobs'][0]
        self.change_binding('A')
        with self.assertRaisesRegex(ValueError,'不能替换本批目标'):
            self.plans.save_batch(self.bindings.get('A'),a['batch_id'])
        self.assertIsNone(self.plans.get('A'))
        self.assertEqual(self.store.next_contact_number(),10)

    def test_gui_polling_fills_A_twice_then_B_and_registers_portuguese_profile(self):
        from app import App
        obj=App.__new__(App)
        obj.store=self.store;obj.adding_queue=self.queue;obj.probing=False
        obj.contact_results=queue.Queue()
        obj.root=SimpleNamespace(after=lambda *args:None)
        obj.probe_button=SimpleNamespace(configure=lambda **kwargs:None)
        obj.status=SimpleNamespace(set=lambda value:None)
        obj.refresh=lambda **kwargs:None;obj.refresh_adding_queue=lambda:None
        class ImmediateThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        for account,number in (('A',7),('A',8),('B',9)):
            job=self.queue.current(qid);self.assertEqual(job['account'],account)
            form=limit(job['window']);form.update(scope='contact_dialog',controls=[])
            filled={'ok':True,'mode':'fill_only','number':str(number),'phone_matches':True,
                'empty_guard':True,'contact_created':False,'final_invite_clicked':False}
            with patch('app.threading.Thread',ImmediateThread),patch('app.inspect_controls',return_value=form),patch('app.fill_contact_test',return_value=filled) as fill:
                obj.tick_contact_queue();obj.poll_contact_worker();obj.poll_contact_worker()
                fill.assert_called_once_with(job['window'],job['item']['value'],str(number),require_empty=True)
            job=self.queue.current(qid);r=profile(job)
            if account=='B':
                r['controls'][2]['name']='Editar contato';r['controls'][3]['name']='Apagar contato'
            with patch('app.threading.Thread',ImmediateThread),patch('app.time.sleep'),patch('app.inspect_controls',side_effect=[r,copy.deepcopy(r)]):
                obj.tick_contact_queue();obj.poll_contact_worker()
            self.assertEqual(self.store.next_contact_number(),number+1)
        self.assertEqual(self.queue.snapshot(qid)['state'],'done')
        self.assertEqual(self.store.addition_stats()['total_added'],3)

    def test_legacy_queue_schema_migration_preserves_existing_success_record(self):
        old=Store(Path(self.tmp.name)/'legacy.sqlite3')
        try:
            old.import_text('phone','+5516991234500')
            b=old.create_batch('A',1);item,_=old.reserve_next(b)
            old.record_add_result(item['id'],'added');before=old.rows()
            old.db.executescript('''
                CREATE TABLE contact_queues(id INTEGER PRIMARY KEY,state TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',created TEXT);
                CREATE TABLE contact_queue_jobs(id INTEGER PRIMARY KEY,queue_id INTEGER NOT NULL,position INTEGER NOT NULL,batch_id INTEGER NOT NULL,window_json TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',item_id INTEGER,preview_number INTEGER,reason TEXT NOT NULL DEFAULT '',UNIQUE(queue_id,position));
                INSERT INTO contact_queues(id,state,created) VALUES(1,'done','2026-09-30');
            ''')
            old.db.execute('INSERT INTO contact_queue_jobs(queue_id,position,batch_id,window_json,state) VALUES(1,1,?,?,?)',(b,json.dumps(window(1)),'target_reached'))
            old.db.commit()
            migrated=ContactQueue(old)
            snap=migrated.snapshot(1)
            self.assertEqual(snap['state'],'done');self.assertEqual(snap['target_mode'],'catalog')
            self.assertEqual(snap['jobs'][0]['target_groups'],[])
            self.assertIsNone(snap['jobs'][0]['pinned_binding'])
            self.assertEqual(old.rows(),before);self.assertEqual(old.next_contact_number(),2)
        finally:old.close()

    def test_clear_preserves_success_held_records_numbers_and_diagnostic_history(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        self.added(qid);job=self.fill(qid);r=profile(job)
        self.queue.save_read(qid,job['id'],[r,copy.deepcopy(r)])
        self.queue.stop(qid,'资料页尚未匹配')
        before=(self.store.rows(),self.store.batches(),self.store.addition_stats())
        result=self.queue.clear()
        self.assertEqual(result['cleared_queue_ids'],[qid])
        self.assertEqual(result['held_items'][0]['id'],job['item_id'])
        self.assertEqual((self.store.rows(),self.store.batches(),self.store.addition_stats()),before)
        self.assertIsNone(self.queue.latest());self.assertIsNone(self.queue.snapshot())
        self.assertIsNone(self.queue.current())
        archived=self.queue.snapshot(qid)
        self.assertEqual(archived['state'],'cleared')
        self.assertIn('资料页尚未匹配',archived['reason'])
        self.assertEqual(archived['jobs'][0]['preview_number'],8)
        with self.assertRaises(ValueError):self.queue.accept(qid,job['id'],[r,r])
        out=Path(self.tmp.name)/'cleared.json';self.queue.export(out)
        exported=json.loads(out.read_text())
        self.assertEqual(exported['id'],qid)
        self.assertEqual(exported['last_read']['reports'],[r,r])
        self.assertEqual(exported['observations'][0]['outcome']['outcome'],'added')

    def test_clear_requires_stopping_running_queue_first(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        before=self.queue.snapshot(qid)
        with self.assertRaisesRegex(ValueError,'停止队列'):self.queue.clear()
        self.assertEqual(self.queue.snapshot(qid),before)
        self.queue.stop(qid);self.queue.clear()
        self.assertEqual(self.store.next_contact_number(),7)

    def test_clear_retires_all_configured_history_and_new_configuration_works(self):
        first=self.queue.configure(self.entries())
        second=self.queue.configure(self.entries((1,1)))
        result=self.queue.clear()
        self.assertEqual(result['cleared_queue_ids'],[first,second])
        self.assertEqual(self.queue.snapshot(first)['state'],'cleared')
        self.assertIsNone(self.queue.latest())
        third=self.queue.configure(self.entries((1,1)))
        self.assertGreater(third,second)
        self.assertEqual(self.queue.latest(),third)
        self.assertEqual(self.queue.snapshot(third)['state'],'configured')
        self.assertEqual(self.store.next_contact_number(),7)

    def test_clear_empty_and_repeated_clear_are_idempotent(self):
        self.assertEqual(self.queue.clear()['cleared_queue_ids'],[])
        qid=self.queue.configure(self.entries());self.queue.clear()
        before=list(self.store.db.execute('SELECT * FROM events'))
        self.assertEqual(self.queue.clear()['cleared_queue_ids'],[])
        self.assertEqual(list(self.store.db.execute('SELECT * FROM events')),before)
        self.assertEqual(self.queue.latest(include_cleared=True),qid)

    def test_cleared_queue_remains_empty_after_restart_but_held_person_blocks_retest(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid);job=self.fill(qid)
        self.queue.stop(qid);self.queue.clear()
        self.store.close();self.store=Store(self.path)
        self.bindings=PinnedGroupBindings(self.store)
        self.queue=ContactQueue(self.store,pinned_groups=self.bindings)
        self.assertIsNone(self.queue.latest());self.assertIsNone(self.queue.current())
        self.assertEqual(self.queue.snapshot(qid)['jobs'][0]['item_id'],job['item_id'])
        with self.assertRaisesRegex(ValueError,'未确认联系人'):self.queue.configure(self.entries())
        self.assertEqual(self.store.next_contact_number(),7)

    def test_old_worker_result_after_clear_cannot_register_or_reserve_next_account(self):
        from app import App
        qid=self.queue.configure(self.entries());self.queue.start(qid);job=self.fill(qid)
        r=profile(job);self.queue.stop(qid);self.queue.clear()
        obj=App.__new__(App);obj.adding_queue=self.queue;obj.probing=True
        obj.contact_results=queue.Queue();obj.contact_results.put(('observe',qid,job,[r,r],None))
        obj.probe_button=SimpleNamespace(configure=lambda **kwargs:None)
        obj.refresh_adding_queue=lambda:None
        obj.poll_contact_worker()
        self.assertFalse(obj.probing)
        self.assertEqual(self.store.next_contact_number(),7)
        self.assertEqual(self.queue.snapshot(qid)['jobs'][1]['item_id'],None)
        self.assertEqual(self.queue.snapshot(qid)['jobs'][0]['item']['status'],'reserved')

    def test_gui_clear_saves_before_clear_report_and_voids_unverified(self):
        from app import App
        qid=self.queue.configure(self.entries());self.queue.start(qid);self.fill(qid)
        self.queue.stop(qid)
        obj=App.__new__(App);obj.adding_queue=self.queue;obj.probing=False
        obj.refresh=lambda:None
        notices=[];obj.show_text=lambda title,text:notices.append(text)
        obj.status=SimpleNamespace(set=lambda value:None)
        with patch('app.DATA',Path(self.tmp.name)):
            obj.clear_adding_queue()
        files=list((Path(self.tmp.name)/'reports').glob('contact_queue_before_clear_*.json'))
        self.assertEqual(len(files),1)
        self.assertEqual(json.loads(files[0].read_text())['state'],'stopped')
        self.assertIsNone(self.queue.latest())
        self.assertIn('1 条未核验记录已作废',notices[0])

    def test_discard_clear_preserves_success_and_numbers_but_voids_hold(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        self.added(qid);job=self.fill(qid);self.queue.stop(qid)
        number=self.store.next_contact_number()
        before=[r for r in self.store.rows() if r['contact_number'] is not None]
        result=self.queue.clear(discard_unverified=True)
        self.assertEqual(result['held_items'],[])
        self.assertEqual([r['id'] for r in result['voided_items']],[job['item_id']])
        self.assertTrue(Path(result['backup_path']).exists())
        self.assertEqual(self.store.next_contact_number(),number)
        self.assertEqual([r for r in self.store.rows() if r['contact_number'] is not None],before)
        self.assertEqual(next(r for r in self.store.rows() if r['id']==job['item_id'])['status'],'void')
        self.assertEqual(self.store.batch(job['batch_id'])['status'],'waiting')
        self.assertEqual(self.plans.get(job['account'])['numbers'],[str(before[0]['contact_number'])])
        again=self.queue.clear(discard_unverified=True)
        self.assertEqual(again['voided_items'],[])

    def test_clear_partial_successes_merge_with_new_batch_and_survive_repeated_clear(self):
        qid=self.queue.configure(self.entries((3,1)));self.queue.start(qid)
        self.added(qid)
        old_batch=self.queue.snapshot(qid)['jobs'][0]['batch_id']
        self.queue.stop(qid,'测试中断')
        self.queue.clear(discard_unverified=True)
        self.assertEqual(self.plans.get('A')['numbers'],['7'])
        old_plan=self.plans.get('A')
        with self.store.db:self.store.db.execute('INSERT INTO pinned_member_states(account,slot,job_json) VALUES(?,?,?)',('A',1,json.dumps({'plan_id':old_plan['plan_id'],'state':'selection_finished'})))
        self.queue.clear(discard_unverified=True)
        self.assertEqual(self.plans.get('A')['plan_id'],old_plan['plan_id'])
        self.assertEqual(self.plans.states('A')[1]['state'],'selection_finished')
        self.assertEqual(self.store.batch(old_batch)['status'],'waiting')
        qid=self.queue.configure(self.entries((1,1)),new_batch=True);self.queue.start(qid)
        self.added(qid);self.added(qid)
        jobs=self.queue.snapshot(qid)['jobs']
        from batch_plan_preparation import BatchPlanPreparation
        prep=BatchPlanPreparation(self.store,self.plans,self.bindings,self.queue)
        preview=prep.preview(qid)
        self.assertEqual(preview['rows'][0]['numbers'],['7','8'])
        prep.confirm(preview)
        plan=self.plans.get('A')
        self.assertEqual(plan['numbers'],['7','8'])
        self.assertEqual(set(plan['batch_ids']),{old_batch,jobs[0]['batch_id']})
        self.plans.validate_batch_plan(plan)
        self.queue.clear(discard_unverified=True)
        self.assertEqual(self.plans.get('A')['numbers'],['7','8'])
        self.assertEqual(self.plans.get('B')['numbers'],['9'])
        self.assertEqual(self.store.next_contact_number(),10)
        with self.store.db:
            for bid in self.plans.get('A')['batch_ids']:
                self.store.db.execute("UPDATE batches SET status='selection_done' WHERE id=?",(bid,))
        self.queue.clear(discard_unverified=True)
        self.assertIsNone(self.plans.get('A'))
        qid=self.queue.configure(self.entries((1,1)),new_batch=True);self.queue.start(qid)
        self.added(qid);self.added(qid)
        self.assertEqual(prep.preview(qid)['rows'][0]['numbers'],['10'])

    def test_merged_success_labels_can_exceed_one_addition_batch(self):
        from visual_members import member_labels
        self.assertEqual(len(member_labels([str(n) for n in range(1,61)])),60)

    def test_discard_clear_running_queue_is_refused(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid)
        before=self.store.rows()
        with self.assertRaises(ValueError):self.queue.clear(discard_unverified=True)
        self.assertEqual(self.store.rows(),before)

    def test_gui_clear_report_write_failure_does_not_retire_queue(self):
        from app import App
        qid=self.queue.configure(self.entries())
        obj=App.__new__(App);obj.adding_queue=self.queue;obj.probing=False
        with patch('app.DATA',Path(self.tmp.name)),patch.object(self.queue,'export',side_effect=OSError('report write failed')):
            with self.assertRaises(OSError):obj.clear_adding_queue()
        self.assertEqual(self.queue.latest(),qid)
        self.assertEqual(self.queue.snapshot(qid)['state'],'configured')

    def observer(self,reports):
        from app import App
        obj=App.__new__(App);obj.adding_queue=self.queue;obj.store=self.store
        obj.probing=False;obj.contact_results=queue.Queue()
        obj.root=SimpleNamespace(after=lambda *args:None)
        obj.probe_button=SimpleNamespace(configure=lambda **kwargs:None)
        obj.status=SimpleNamespace(set=lambda value:None)
        obj.refresh=lambda **kwargs:None;obj.refresh_adding_queue=lambda:None
        class ImmediateThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        with patch('app.threading.Thread',ImmediateThread),patch('app.time.sleep'),patch('app.inspect_controls',side_effect=reports),patch('app.run_window_script'),patch('app.fill_contact_test') as fill:
            obj.tick_contact_queue();obj.poll_contact_worker()
            fill.assert_not_called()

    def ordinary_window(self,job):
        r=limit(job['window'])
        r.update(scope='window',scope_class='class MainWindow',scope_runtime_id='main-window',
            truncated=True,visited=400,controls=[])
        return r

    def test_capped_chat_can_request_guarded_open_without_filling_or_consuming_number(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid);self.added(qid)
        job=self.queue.current(qid);self.observer([self.ordinary_window(job)])
        self.assertEqual(self.queue.snapshot(qid)['state'],'running')
        self.assertEqual(self.queue.current(qid)['state'],'opening')
        self.assertEqual(self.queue.current(qid)['item_id'],job['item_id'])
        self.assertEqual(self.store.next_contact_number(),8)

    def test_capped_chat_while_waiting_saved_profile_keeps_filled_user_unconfirmed(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid);job=self.fill(qid)
        r=self.ordinary_window(job);self.observer([r,copy.deepcopy(r)])
        self.assertEqual(self.queue.snapshot(qid)['state'],'running')
        self.assertEqual(self.queue.current(qid)['state'],'filled')
        self.assertEqual(self.queue.current(qid)['item']['status'],'reserved')
        self.assertEqual(self.store.next_contact_number(),7)

    def test_transition_from_capped_chat_to_complete_profile_waits_for_two_profile_reads(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid);job=self.fill(qid)
        r=profile(job);self.observer([self.ordinary_window(job),r])
        self.assertEqual(self.queue.current(qid)['state'],'filled')
        self.assertEqual(self.store.next_contact_number(),7)
        self.observer([r,copy.deepcopy(r)])
        self.assertEqual(self.store.next_contact_number(),8)

    def test_incomplete_dialog_cannot_be_used_to_fill_and_wrong_window_still_stops(self):
        for mutation in ('dialog','wrong_window','errors'):
            qid=self.queue.configure(self.entries());self.queue.start(qid)
            job=self.queue.current(qid);r=self.ordinary_window(job)
            if mutation=='dialog':r['scope']='contact_dialog';r['scope_class']='class Ui::BoxLayerWidget'
            elif mutation=='wrong_window':r['window_handle']+=1
            else:r['errors']=['unavailable']
            self.observer([r])
            self.assertEqual(self.queue.snapshot(qid)['state'],'review',mutation)
            self.assertEqual(self.store.next_contact_number(),7)
            # Reset only this synthetic test's never-filled reservation between cases.
            self.store.db.execute('DELETE FROM contact_queue_reads');self.store.db.execute('DELETE FROM contact_queue_jobs')
            self.store.db.execute('DELETE FROM contact_queues')
            self.store.db.execute("UPDATE items SET status='ready',batch_id=NULL,account=NULL WHERE status='reserved'")
            self.store.db.execute('DELETE FROM batches');self.store.db.commit()

    def test_restore_cleared_unfilled_user_copies_job_ids_and_keeps_A7_then_B9(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid);self.added(qid)
        old_job=self.queue.current(qid);self.queue.stop(qid);self.queue.clear()
        before=(self.store.rows(),self.store.batches(),self.store.next_contact_number())
        restored=self.queue.restore_waiting(qid)
        self.assertNotEqual(restored,qid)
        self.assertEqual((self.store.rows(),self.store.batches(),self.store.next_contact_number()),before)
        job=self.queue.start(restored)
        self.assertNotEqual(job['id'],old_job['id'])
        self.assertEqual(job['item_id'],old_job['item_id'])
        self.assertEqual(self.queue.snapshot(restored)['jobs'][0]['added_numbers'],[7])
        self.assertEqual(self.added(restored)['outcome'],'added')
        self.assertEqual(self.queue.current(restored)['account'],'B')
        self.assertEqual(self.added(restored)['outcome'],'added')
        self.assertEqual([j['added_numbers'] for j in self.queue.snapshot(restored)['jobs']],[[7,8],[9]])
        self.assertEqual(self.store.next_contact_number(),10)

    def test_restore_rejects_previously_filled_or_uncertain_task_and_changed_binding(self):
        for mutation in ('filled','history','uncertain','binding'):
            qid=self.queue.configure(self.entries());self.queue.start(qid)
            job=self.fill(qid) if mutation=='filled' else self.queue.current(qid)
            if mutation=='history':self.store._event('contact_queue_fill_started',job['item_id'],job['batch_id'],'prior attempt');self.store.db.commit()
            if mutation=='uncertain':self.store.db.execute("UPDATE items SET status='uncertain' WHERE id=?",(job['item_id'],));self.store.db.commit()
            if mutation=='binding':self.change_binding('A')
            self.queue.stop(qid);self.queue.clear()
            before=self.store.rows()
            with self.assertRaises(ValueError):self.queue.restore_waiting(qid)
            self.assertEqual(self.queue.latest(include_cleared=True),qid)
            self.assertEqual(self.store.rows(),before)
            self.assertEqual(self.store.next_contact_number(),7)
            self.store.db.execute('DELETE FROM contact_queue_reads');self.store.db.execute('DELETE FROM contact_queue_jobs')
            self.store.db.execute('DELETE FROM contact_queues');self.store.db.execute('DELETE FROM events')
            self.store.db.execute("UPDATE items SET status='ready',batch_id=NULL,account=NULL WHERE status IN ('reserved','uncertain')")
            self.store.db.execute('DELETE FROM batches');self.store.db.commit()

    def test_restore_does_not_allow_duplicate_configured_copy(self):
        qid=self.queue.configure(self.entries());self.queue.start(qid);self.queue.stop(qid);self.queue.clear()
        restored=self.queue.restore_waiting(qid)
        with self.assertRaisesRegex(ValueError,'重复恢复'):self.queue.restore_waiting(qid)
        self.assertEqual(self.queue.latest(),restored)
        with self.assertRaises(ValueError):self.queue.accept(qid,self.queue.snapshot(qid)['jobs'][0]['id'],[])


if __name__=='__main__':unittest.main()
