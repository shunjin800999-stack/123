"""New scans relabel windows; only a started fresh plan replaces invitation lists."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import App
from batch_plan_preparation import BatchPlanPreparation
from contact_queue import ContactQueue
from group_cleanup import saved_addition_contacts
from groups import GroupCatalog
from pinned_groups import PinnedGroupBindings
from pinned_members import PinnedMemberPlans
from selection_scope import current_queue, jobs
from store import Store
from test_contact_queue import profile, window
from test_pinned_groups import report
from windows_scan import number_scanned_windows


class SelectionScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'progress.sqlite3'
        self.store=Store(self.path);self.addCleanup(lambda:self.store.close())
        self.catalog=GroupCatalog(self.store)
        self.bindings=PinnedGroupBindings(self.store);self.plans=PinnedMemberPlans(self.store)
        self.queue=ContactQueue(self.store,group_catalog=self.catalog,pinned_groups=self.bindings)
        self.bulk=BatchPlanPreparation(self.store,self.plans,self.bindings,self.queue)
        self.store.import_text('phone','\n'.join(f'+55169912345{i:02d}' for i in range(30)))
        for i in (1,2):
            read=report(window(i))
            self.bindings.save(f'账号{i}',window(i),read,copy.deepcopy(read))
        self.old_queue=self.configure(replace=False)
        self.queue.start(self.old_queue)
        self.add(self.old_queue);self.add(self.old_queue)
        self.bulk.confirm(self.bulk.preview(self.old_queue))
        self.old_plans=[self.plans.get(f'账号{i}') for i in (1,2)]
        self.old_batches=[p['batch_id'] for p in self.old_plans]
        self.old_rows=copy.deepcopy(self.store.rows())

    def configure(self,replace=True,order=(1,2),target=1):
        return self.queue.configure([{'account':f'账号{i}','window':window(w),'target':target}
            for i,w in enumerate(order,1)],new_batch=True,independent=True,replace_selection=replace)

    def add(self,queue_id):
        job=self.queue.current(queue_id);job=self.queue.claim_fill(queue_id,job['id'])
        self.queue.finish_fill(queue_id,job['id'],{'ok':True,'mode':'fill_only','number':str(job['preview_number']),
            'phone_matches':True,'empty_guard':True,'contact_created':False,'final_invite_clicked':False})
        job=self.queue.current(queue_id);read=profile(job)
        return self.queue.accept(queue_id,job['id'],[read,copy.deepcopy(read)])

    def start_new(self,**kwargs):
        queue_id=self.configure(**kwargs);self.queue.start(queue_id);return queue_id

    def test_configuring_and_scanning_alone_preserve_old_active_lists(self):
        self.configure()
        app=App.__new__(App);app.probing=False;app.all_windows=Mock();app.all_windows.get.return_value=False
        app.window_tree=Mock();app.window_tree.get_children.return_value=();app.window_count=Mock()
        with patch('app.scan_windows',return_value=[window(2),window(1)]):app.scan()
        self.assertEqual([r['scan_account'] for r in app.windows],['账号1','账号2'])
        self.assertEqual([r['hwnd'] for r in app.windows],[102,101])
        self.assertIsNone(current_queue(self.store.db))
        self.assertEqual([self.plans.get(f'账号{i}') for i in (1,2)],self.old_plans)
        self.assertEqual(self.store.rows(),self.old_rows)

    def test_started_plan_immediately_retires_old_lists_and_preserves_successes(self):
        queue_id=self.start_new()
        self.assertEqual(current_queue(self.store.db),queue_id)
        self.assertIsNone(self.plans.get('账号1'));self.assertIsNone(self.plans.get('账号2'))
        for batch_id in self.old_batches:
            with self.assertRaisesRegex(ValueError,'旧添加计划'):self.plans.batch_members(batch_id,allow_completed=True)
        current_rows={r['id']:r for r in self.store.rows()}
        for old in self.old_rows:
            if old['contact_number'] is not None:self.assertEqual(current_rows[old['id']],old)
        self.assertEqual(self.store.next_contact_number(),3)
        history=json.loads(self.store.db.execute('SELECT retired_json FROM contact_selection_scope_history WHERE queue_id=?',(queue_id,)).fetchone()[0])
        self.assertEqual(len(history['pinned_member_plans']),2)
        self.assertEqual(len(self.store.db.execute('SELECT * FROM pinned_batch_member_plans').fetchall()),2)

    def test_stopping_before_any_new_success_never_falls_back_to_old_lists(self):
        queue_id=self.start_new();self.queue.stop(queue_id)
        self.assertEqual(current_queue(self.store.db),queue_id)
        self.assertIsNone(self.plans.get('账号1'))
        with self.assertRaises(ValueError):self.plans.entries([self.bindings.get('账号1')],1)
        self.assertEqual(self.store.next_contact_number(),3)

    def test_bulk_and_both_group_entries_use_only_new_success_numbers(self):
        queue_id=self.start_new();self.add(queue_id);self.add(queue_id)
        preview=self.bulk.preview(queue_id)
        self.assertEqual([r['numbers'] for r in preview['rows']],[['3'],['4']])
        self.bulk.confirm(preview)
        records=[self.bindings.get(f'账号{i}') for i in (1,2)]
        for slot in (2,1,2):
            self.assertEqual([p['numbers'] for p in self.plans.entries(records,slot)],[['3'],['4']])
        self.assertTrue(all(p['selection_queue_id']==queue_id for p in (self.plans.get('账号1'),self.plans.get('账号2'))))
        self.assertEqual(self.store.next_contact_number(),5)

    def test_reversed_windows_rebind_groups_without_inheriting_other_contacts(self):
        queue_id=self.start_new(order=(2,1))
        self.assertEqual(self.bindings.get('账号1')['window'],window(2))
        self.assertEqual(self.bindings.get('账号2')['window'],window(1))
        self.assertEqual([j['window']['hwnd'] for j in jobs(self.store.db)],[102,101])
        self.add(queue_id);self.add(queue_id)
        self.bulk.confirm(self.bulk.preview())
        self.assertEqual(self.plans.get('账号1')['numbers'],['3'])
        self.assertEqual(self.plans.get('账号1')['record']['window'],window(2))
        with self.assertRaises(ValueError):self.plans.save_batch(self.old_plans[0]['record'],self.old_batches[0])

    def test_clear_after_partial_success_does_not_merge_previous_plan(self):
        queue_id=self.start_new(target=3);self.add(queue_id);self.queue.stop(queue_id)
        self.queue.clear(discard_unverified=True)
        kept=[r[0] for r in self.store.db.execute('SELECT batch_id FROM selection_carry_batches')]
        self.assertEqual(kept,[self.queue.snapshot(queue_id)['jobs'][0]['batch_id']])
        self.assertEqual(self.plans.get('账号1')['numbers'],['3'])
        self.assertIsNone(self.plans.get('账号2'))
        new_id=self.configure()
        self.assertEqual(self.plans.get('账号1')['numbers'],['3'])
        self.queue.start(new_id);self.add(new_id);self.add(new_id)
        self.bulk.confirm(self.bulk.preview())
        self.assertEqual(self.plans.get('账号1')['numbers'],['4'])
        self.assertEqual(self.plans.get('账号2')['numbers'],['5'])
        self.assertEqual(self.store.next_contact_number(),6)

    def test_carry_injected_from_old_history_cannot_expand_new_members(self):
        queue_id=self.start_new();self.add(queue_id);self.add(queue_id)
        self.plans.ensure_carry_table()
        with self.store.db:
            self.store.db.execute('INSERT INTO selection_carry_batches VALUES(?,?)',(self.old_batches[0],'账号1'))
        new_batch=self.queue.snapshot(queue_id)['jobs'][0]['batch_id']
        self.assertEqual(self.plans.batch_members(new_batch)[1][0]['number'],'3')
        self.assertEqual(len(self.plans.batch_members(new_batch)[1]),1)

    def test_old_frozen_plan_cannot_be_restored_by_old_history_buttons(self):
        self.start_new()
        for old in self.old_plans:
            with self.assertRaisesRegex(ValueError,'旧添加计划'):self.plans.validate_batch_plan(old,allow_completed=True)
            with self.assertRaises(ValueError):self.plans.save_batch(old['record'],old['batch_id'])
        with self.store.db:
            self.store.db.execute('INSERT INTO pinned_member_plans VALUES(?,?)',('账号1',json.dumps(self.old_plans[0])))
        self.assertIsNone(self.plans.get('账号1'))
        self.plans.finish_existing_selections()  # Old restored JSON cannot become live.
        self.assertIsNone(self.plans.get('账号1'))

    def test_new_scope_survives_restart_and_does_not_reactivate_old_cache(self):
        queue_id=self.start_new();self.add(queue_id);self.add(queue_id)
        self.bulk.confirm(self.bulk.preview())
        self.store.close();self.store=Store(self.path)
        self.plans=PinnedMemberPlans(self.store);self.queue=ContactQueue(self.store)
        self.assertEqual(current_queue(self.store.db),queue_id)
        self.assertEqual(self.plans.get('账号1')['numbers'],['3'])
        self.assertEqual(self.plans.get('账号2')['numbers'],['4'])
        self.assertEqual(self.store.next_contact_number(),5)

    def test_start_activation_failure_rolls_back_queue_binding_and_active_lists(self):
        queue_id=self.configure(order=(2,1))
        self.store.db.execute('''CREATE TEMP TRIGGER fail_scope BEFORE INSERT ON contact_selection_scope
            BEGIN SELECT RAISE(ABORT,'scope failure'); END''')
        self.store.db.commit()
        before=self.store.rows()
        with self.assertRaises(Exception):self.queue.start(queue_id)
        self.assertEqual(self.queue.snapshot(queue_id)['state'],'configured')
        self.assertEqual(self.store.rows(),before)
        self.assertEqual([self.plans.get(f'账号{i}') for i in (1,2)],self.old_plans)
        self.assertEqual(self.bindings.get('账号1')['window'],window(1))
        self.assertIsNone(current_queue(self.store.db))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM contact_selection_scope_history').fetchone()[0],0)

    def test_invalid_new_plan_does_not_retire_anything(self):
        for args in ({'replace_selection':True,'independent':False,'new_batch':True},
                     {'replace_selection':True,'independent':True,'new_batch':False}):
            with self.assertRaises(ValueError):self.queue.configure([{'account':'账号1','window':window(1),'target':1}],**args)
        self.assertIsNone(current_queue(self.store.db))
        self.assertEqual(self.plans.get('账号1'),self.old_plans[0])

    def test_prior_held_item_stays_held_and_is_never_reassigned_under_reused_label(self):
        with self.store.db:self.store.db.execute("UPDATE batches SET status='archived' WHERE id=?",(self.old_batches[0],))
        prior=self.store.create_batch('账号1',3)
        held,_=self.store.reserve_next(prior)
        queue_id=self.start_new(order=(2,1))
        job=self.queue.current(queue_id)
        self.assertNotEqual(job['item_id'],held['id'])
        row=next(r for r in self.store.rows() if r['id']==held['id'])
        self.assertEqual(row['status'],'reserved');self.assertEqual(row['batch_id'],prior)
        self.assertIsNone(row['contact_number'])

    def test_reordered_profile_cleanup_uses_exact_window_history_not_old_label(self):
        record={'account':'账号1','window':window(2),'contact_only':True,'targets':[]}
        self.assertEqual(saved_addition_contacts(self.store,record),[])
        contacts=saved_addition_contacts(self.store,record,window_history=True)
        self.assertEqual([r['number'] for r in contacts],['2'])
        self.assertNotIn('1',[r['number'] for r in contacts])

    def test_reopened_window_requires_a_new_pinned_observation_after_start(self):
        changed=window(1);changed['hwnd']+=100;changed['pid']+=100
        queue_id=self.queue.configure([{'account':'账号1','window':changed,'target':1}],
            independent=True,new_batch=True,replace_selection=True)
        self.queue.start(queue_id)
        self.assertIsNone(self.bindings.get('账号1'))
        self.add(queue_id)
        read=report(changed)
        self.bindings.save('账号1',changed,read,copy.deepcopy(read))
        self.bulk.confirm(self.bulk.preview())
        self.assertEqual(self.plans.get('账号1')['numbers'],['3'])

    def test_previous_preview_and_pre_start_input_do_not_restore_the_old_plan(self):
        old_preview=self.bulk.preview(self.old_queue)
        self.start_new()
        with self.assertRaises(ValueError):self.bulk.confirm(old_preview)
        self.assertIsNone(self.plans.get('账号1'))

    def test_ui_configuration_uses_scan_labels_and_enables_scope_replacement(self):
        app=App.__new__(App);app.probing=False;app.adding_queue=Mock()
        app.adding_queue.current.return_value=None
        app.windows=number_scanned_windows([window(2),window(1)])
        app.window_tree=Mock();app.window_tree.selection.return_value=('1','0')
        app.window_tree.get_children.return_value=('0','1')
        app.root=Mock();app.refresh=Mock();app.status=Mock();app.guarded=lambda operation:operation
        buttons=[]
        def button(parent,**kwargs):
            buttons.append(kwargs);return Mock()
        def variable(*args,**kwargs):
            return Mock(get=Mock(return_value=kwargs['value']))
        with patch('app.tk.Toplevel'),patch('app.tk.Canvas'),patch('app.tk.StringVar',side_effect=variable),\
             patch('app.ttk.Label'),patch('app.ttk.Frame'),patch('app.ttk.Scrollbar'),patch('app.ttk.Combobox'),\
             patch('app.ttk.Entry') as entry,patch('app.ttk.Spinbox'),patch('app.ttk.Button',side_effect=button):
            app.configure_adding_queue()
            next(b['command'] for b in buttons if b['text']=='保存账号队列')()
        plan=app.adding_queue.configure.call_args.args[0]
        self.assertEqual([r['account'] for r in plan],['账号1','账号2'])
        self.assertEqual([r['window']['hwnd'] for r in plan],[102,101])
        self.assertEqual([r['target'] for r in plan],[20,20])
        self.assertTrue(app.adding_queue.configure.call_args.kwargs['replace_selection'])
        self.assertTrue(all(c.kwargs['state']=='readonly' for c in entry.call_args_list))


class ScanNumberingTests(unittest.TestCase):
    def test_each_scan_starts_at_one_and_non_telegram_rows_do_not_consume_numbers(self):
        original=[window(8),dict(window(7),candidate=False),window(3)]
        before=copy.deepcopy(original);rows=number_scanned_windows(original)
        self.assertEqual([r['scan_account'] for r in rows],['账号1','','账号2'])
        self.assertEqual(original,before)
        again=number_scanned_windows([window(3),window(8)])
        self.assertEqual([r['scan_account'] for r in again],['账号1','账号2'])
        self.assertEqual([r['hwnd'] for r in again],[103,108])

    def test_scan_while_plan_is_running_does_not_change_its_frozen_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db');self.addCleanup(store.close)
            queue=ContactQueue(store);store.import_text('phone','+5516991234567')
            queue_id=queue.configure([{'account':'账号1','window':window(1),'target':1}],
                independent=True,new_batch=True,replace_selection=True)
            queue.start(queue_id);before=queue.snapshot(queue_id)
            app=App.__new__(App);app.probing=False;app.all_windows=Mock();app.all_windows.get.return_value=False
            app.window_tree=Mock();app.window_tree.get_children.return_value=();app.window_count=Mock()
            with patch('app.scan_windows',return_value=[window(2),window(1)]):app.scan()
            self.assertEqual(app.windows[1]['scan_account'],'账号2')
            self.assertEqual(queue.snapshot(queue_id),before)
            self.assertEqual(current_queue(store.db),queue_id)


if __name__=='__main__':unittest.main()
