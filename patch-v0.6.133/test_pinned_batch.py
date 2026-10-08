import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from pinned_members import PinnedMemberPlans, select_pinned_member_queue
from store import Store
from test_group_navigation import observation
from test_pinned_members import selected


class PinnedBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.store=Store(self.path/'db.sqlite3');self.plans=PinnedMemberPlans(self.store)
        self.store.import_text('phone','\n'.join(f'+55169912345{i:02d}' for i in range(10)))
        self.store.import_text('username','\n'.join(f'@example_{i}' for i in range(10)))
        self.record=observation(1,'A');self.batch=self.store.create_batch('A',2)

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def add(self,batch=None,outcome='added'):
        row,_=self.store.reserve_next(batch or self.batch)
        number=self.store.record_add_result(row['id'],outcome)
        return row['id'],number

    def freeze(self):
        self.add();self.add()
        return self.plans.save_batch(self.record,self.batch)

    def select(self,slot,plan=None):
        plan=plan or self.plans.get('A')
        job={'account':'A','slot':slot,'plan_id':plan['plan_id'],'numbers':plan['numbers'],
            'window':self.record['window'],'state':'waiting_for_manual_invite',
            'selection':selected(self.record,slot,plan['numbers'])}
        self.plans.save_run({'scope':'pinned_member_selection_queue','jobs':[job],
            'final_invite_clicked':False,'contact_database_updated':False})

    def test_actual_global_success_numbers_not_account_quota_ranges(self):
        self.add();b=self.store.create_batch('B',1);self.add(b);self.add()
        stats=self.store.addition_stats();before=self.store.rows()
        plan=self.plans.save_batch(self.record,self.batch)
        self.assertEqual(plan['numbers'],['1','3']);self.assertEqual(len(plan['members']),2)
        self.assertEqual(self.store.next_contact_number(),4)
        self.assertEqual(self.store.addition_stats(),stats);self.assertEqual(self.store.rows(),before)

    def test_empty_incomplete_and_wrong_account_cannot_generate(self):
        with self.assertRaisesRegex(ValueError,'没有'):self.plans.save_batch(self.record,self.batch)
        self.add()
        with self.assertRaisesRegex(ValueError,'尚未达到'):self.plans.save_batch(self.record,self.batch)
        self.add()
        with self.assertRaisesRegex(ValueError,'不属于'):self.plans.save_batch(observation(2,'B'),self.batch)
        self.assertEqual(self.plans.get('A'),None)

    def test_partial_paused_uses_only_success_keeps_uncertain(self):
        self.store.db.execute('UPDATE batches SET target=20 WHERE id=?',(self.batch,));self.store.db.commit()
        good,_=self.add();held,_=self.add(outcome='restriction')
        plan=self.plans.save_batch(self.record,self.batch)
        self.assertEqual(plan['numbers'],['1']);self.assertEqual(plan['members'][0]['item_id'],good)
        self.assertEqual(next(r for r in self.store.rows() if r['id']==held)['status'],'uncertain')
        self.assertEqual(self.store.batch(self.batch)['status'],'paused')

    def test_explicit_finish_partial_batch_without_fabricating_quota(self):
        self.add();stats=self.store.addition_stats()
        plan=self.plans.save_batch(self.record,self.batch,finish_adding=True)
        self.assertEqual(plan['numbers'],['1']);self.assertEqual(self.store.batch(self.batch)['status'],'waiting')
        self.assertEqual(self.store.addition_stats(),stats)
        self.assertEqual(self.plans.entries([self.record],1)[0]['numbers'],['1'])
        with self.assertRaises(ValueError):self.store.reserve_next(self.batch)

    def test_finish_partial_refuses_held_record_and_rolls_back(self):
        self.add();self.store.reserve_next(self.batch)
        with self.assertRaisesRegex(ValueError,'未确认'):self.plans.save_batch(self.record,self.batch,finish_adding=True)
        self.assertEqual(self.store.batch(self.batch)['status'],'active');self.assertIsNone(self.plans.get('A'))

    def test_failed_sources_not_members(self):
        bad,_=self.add(outcome='ordinary_failure');self.add();self.add()
        plan=self.plans.save_batch(self.record,self.batch)
        self.assertEqual(plan['numbers'],['1','2'])
        self.assertNotIn(bad,[m['item_id'] for m in plan['members']])

    def test_invalid_flags_or_inconsistent_numbered_rows_rejected(self):
        self.add();self.add()
        for field,value in (('numbering_global',2),('added_at',''),('account','B'),('status','uncertain')):
            row=self.store.rows()[0]
            self.store.db.execute(f'UPDATE items SET {field}=? WHERE id=?',(value,row['id']));self.store.db.commit()
            with self.assertRaisesRegex(ValueError,'未确认或编号'):self.plans.save_batch(self.record,self.batch)
            self.store.db.execute(f'UPDATE items SET {field}=? WHERE id=?',(row[field],row['id']));self.store.db.commit()

    def test_legacy_success_event_preserves_numbers_flags_dates_and_counts(self):
        self.add();self.add()
        self.store.db.execute('UPDATE items SET numbering_global=0 WHERE batch_id=?',(self.batch,));self.store.db.commit()
        rows=self.store.rows();stats=self.store.addition_stats();sequence=self.store.next_contact_number()
        plan=self.plans.save_batch(self.record,self.batch)
        self.assertEqual(plan['numbers'],['1','2'])
        self.assertEqual(plan['legacy_item_ids'],[m['item_id'] for m in plan['members']])
        self.assertEqual(self.store.rows(),rows);self.assertEqual(self.store.addition_stats(),stats)
        self.assertEqual(self.store.next_contact_number(),sequence)
        self.store.close();self.store=Store(self.path/'db.sqlite3');self.plans=PinnedMemberPlans(self.store)
        self.assertEqual(self.plans.save_batch(self.record,self.batch),plan)
        self.assertEqual(self.plans.entries([self.record],1)[0]['numbers'],['1','2'])

    def test_legacy_requires_success_event_for_same_item_batch_and_date(self):
        first,_=self.add();self.add()
        self.store.db.execute('UPDATE items SET numbering_global=0 WHERE id=?',(first,));self.store.db.commit()
        for mutation in ("UPDATE events SET action='uncertain' WHERE action='added'", 
                         "UPDATE events SET item_id=999 WHERE action='added'",
                         "UPDATE events SET batch_id=NULL WHERE action='added'",
                         "UPDATE events SET time='2000-01-01 00:00:00' WHERE action='added'"):
            self.store.db.execute('SAVEPOINT proof_check')
            self.store.db.execute(mutation)
            with self.assertRaisesRegex(ValueError,'缺少对应'):self.plans.batch_members(self.batch)
            self.store.db.execute('ROLLBACK TO proof_check');self.store.db.execute('RELEASE proof_check')
        self.assertIsNone(self.plans.get('A'))

    def test_legacy_same_historical_number_in_other_account_is_not_renumbered(self):
        self.add();self.add();b=self.store.create_batch('B',1);other,_=self.add(b)
        self.store.db.execute('UPDATE items SET numbering_global=0 WHERE contact_number IS NOT NULL')
        self.store.db.execute('UPDATE items SET contact_number=1 WHERE id=?',(other,));self.store.db.commit()
        a_plan=self.plans.save_batch(self.record,self.batch)
        b_plan=self.plans.save_batch(observation(2,'B'),b)
        self.assertEqual(a_plan['numbers'],['1','2']);self.assertEqual(b_plan['numbers'],['1'])
        self.assertNotIn(other,[m['item_id'] for m in a_plan['members']])
        self.assertEqual(self.store.next_contact_number(),4)

    def test_legacy_group_acks_do_not_convert_flags_or_change_addition_dates(self):
        self.add();self.add()
        self.store.db.execute('UPDATE items SET numbering_global=0 WHERE batch_id=?',(self.batch,));self.store.db.commit()
        before=[(r['id'],r['contact_number'],r['numbering_global'],r['added_at']) for r in self.store.rows()]
        stats=self.store.addition_stats();self.plans.save_batch(self.record,self.batch)
        for slot in (1,2):self.select(slot);self.plans.confirm_group_invited('A',slot)
        self.assertEqual(before,[(r['id'],r['contact_number'],r['numbering_global'],r['added_at']) for r in self.store.rows()])
        self.assertEqual(self.store.addition_stats(),stats)

    def test_invalid_legacy_proof_rolls_back_partial_finish_without_archive(self):
        item,_=self.add()
        self.store.db.execute('UPDATE items SET numbering_global=0 WHERE id=?',(item,))
        self.store.db.execute("DELETE FROM events WHERE action='added'");self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'缺少对应'):self.plans.save_batch(self.record,self.batch,finish_adding=True)
        self.assertEqual(self.store.batch(self.batch)['status'],'active')
        self.assertIsNone(self.plans.get('A'))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM pinned_batch_member_plans').fetchone()[0],0)

    def test_restart_and_repeated_save_preserve_frozen_plan(self):
        plan=self.freeze();self.select(1)
        self.store.close();self.store=Store(self.path/'db.sqlite3');self.plans=PinnedMemberPlans(self.store)
        self.assertEqual(self.plans.save_batch(self.record,self.batch),plan)
        self.assertEqual(self.plans.entries([self.record],2)[0]['numbers'],['1','2'])

    def test_changed_binding_or_members_cannot_replace_frozen_plan(self):
        original=self.freeze();changed=copy.deepcopy(self.record);changed['targets'][0]['name']='other'
        with self.assertRaisesRegex(ValueError,'已冻结'):self.plans.save_batch(changed,self.batch)
        self.store.db.execute('UPDATE items SET contact_number=99 WHERE id=?',(original['members'][0]['item_id'],));self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'发生变化'):self.plans.entries([self.record],1)
        self.assertEqual(self.plans.get('A'),original)

    def test_selection_alone_and_single_group_ack_do_not_complete_batch(self):
        plan=self.freeze();stats=self.store.addition_stats();self.select(1)
        self.assertEqual(self.store.batch(self.batch)['status'],'active')
        with self.assertRaises(ValueError):self.plans.confirm_group_invited('A',2)
        result=self.plans.confirm_group_invited('A',1)
        self.assertFalse(result['all_groups_confirmed'])
        self.assertEqual(self.store.batch(self.batch)['status'],'waiting')
        self.assertEqual({r['status'] for r in self.store.rows() if r['batch_id']==self.batch},{'pending_invite'})
        with self.assertRaises(ValueError):self.store.confirm_invited([m['item_id'] for m in plan['members']])
        with self.assertRaises(ValueError):self.store.create_batch('A',2)
        self.assertEqual(self.plans.entries([self.record],1)[0]['numbers'],plan['numbers'])
        self.assertEqual(self.plans.entries([self.record],2)[0]['numbers'],plan['numbers'])
        self.assertEqual(self.plans.states('A')[1]['state'],'confirmed_invited')
        self.assertEqual(self.store.addition_stats(),stats)

    def test_old_proven_selections_finish_without_contact_changes(self):
        self.freeze();self.select(1);before=self.store.rows()
        self.plans.finish_existing_selections()
        self.assertEqual(self.store.batch(self.batch)['status'],'active')
        self.select(2);self.plans.finish_existing_selections()
        self.assertEqual(self.store.batch(self.batch)['status'],'selection_done')
        self.assertEqual(self.store.rows(),before)
        self.assertEqual(self.plans.states('A')[2]['state'],'selection_finished')
        self.plans.finish_existing_selections()
        self.assertEqual(self.store.rows(),before)

    def test_two_selection_finished_states_end_task_without_invitation_records(self):
        plan=self.freeze();before=self.store.rows();stats=self.store.addition_stats()
        for slot in (1,2):
            job={'account':'A','slot':slot,'plan_id':plan['plan_id'],'numbers':plan['numbers'],
                 'window':self.record['window'],'state':'selection_finished',
                 'selection':selected(self.record,slot,plan['numbers'])}
            self.plans.save_run({'scope':'pinned_member_selection_queue','jobs':[job],
                                'final_invite_clicked':False,'contact_database_updated':False})
            if slot==1:
                self.assertEqual(self.store.batch(self.batch)['status'],'active')
                self.assertEqual(self.plans.entries([self.record],2)[0]['numbers'],plan['numbers'])
                self.assertEqual(self.plans.states('A')[1]['state'],'selection_finished')
        self.assertEqual(self.store.batch(self.batch)['status'],'selection_done')
        self.assertEqual(self.store.rows(),before)
        self.assertEqual(self.store.addition_stats(),stats)
        self.assertFalse(any(r['status']=='completed' for r in self.store.rows()))
        self.store.create_batch('A',2)

    def test_two_acks_complete_exact_members_once_and_allow_next_batch(self):
        plan=self.freeze();dates=[r['added_at'] for r in self.store.rows()];stats=self.store.addition_stats()
        self.select(1);self.plans.confirm_group_invited('A',1);self.select(2)
        result=self.plans.confirm_group_invited('A',2)
        self.assertTrue(result['all_groups_confirmed']);self.assertEqual(self.store.batch(self.batch)['status'],'completed')
        self.assertTrue(self.plans.confirm_group_invited('A',2)['already_confirmed'])
        self.assertEqual(self.store.addition_stats(),stats);self.assertEqual(self.store.next_contact_number(),3)
        self.assertEqual([r['added_at'] for r in self.store.rows()],dates)
        with self.assertRaises(ValueError):self.plans.save_batch(self.record,self.batch)
        new=self.store.create_batch('A',1);_,number=self.add(new);self.assertEqual(number,3)
        next_plan=self.plans.save_batch(self.record,new)
        self.assertNotEqual(next_plan['plan_id'],plan['plan_id'])
        with self.assertRaises(ValueError):self.plans.confirm_group_invited('A',1)

    def test_two_acks_preserve_paused_unresolved_task(self):
        self.store.db.execute('UPDATE batches SET target=20 WHERE id=?',(self.batch,));self.store.db.commit()
        self.add();held,_=self.add(outcome='uncertain');self.plans.save_batch(self.record,self.batch)
        for slot in (1,2):self.select(slot);result=self.plans.confirm_group_invited('A',slot)
        self.assertTrue(result['all_groups_confirmed']);self.assertEqual(result['held_items'],1)
        self.assertEqual(self.plans.confirm_group_invited('A',2)['held_items'],1)
        self.assertEqual(self.store.batch(self.batch)['status'],'review')
        self.assertEqual(next(r for r in self.store.rows() if r['id']==held)['status'],'uncertain')

    def test_test_plan_missing_or_invalid_selection_cannot_ack(self):
        self.plans.save(self.record,['1','2'])
        with self.assertRaises(ValueError):self.plans.confirm_group_invited('A',1)
        self.freeze()
        with self.assertRaises(ValueError):self.plans.confirm_group_invited('A',1)
        self.select(1);job=self.plans.states('A')[1];job['selection']['selected_numbers']=['1']
        self.store.db.execute('UPDATE pinned_member_states SET job_json=? WHERE account=? AND slot=1',(json.dumps(job),'A'));self.store.db.commit()
        with self.assertRaises(ValueError):self.plans.confirm_group_invited('A',1)

    def test_frozen_batch_prevents_new_success_number(self):
        self.freeze();self.store.db.execute('UPDATE batches SET target=3 WHERE id=?',(self.batch,));self.store.db.commit()
        row,_=self.store.reserve_next(self.batch)
        with self.assertRaisesRegex(ValueError,'已冻结'):self.store.record_add_result(row['id'],'added')
        self.assertEqual(self.store.next_contact_number(),3)

    def test_worker_rejects_inconsistent_db_snapshot_before_navigation(self):
        plan=self.freeze();plan['members'][0]['number']='9'
        with patch('pinned_members.open_pinned_groups') as native:
            with self.assertRaises(ValueError):select_pinned_member_queue([plan],1,self.path/'bad.json')
        native.assert_not_called()

    def test_worker_accepts_database_source_and_confirmed_first_group(self):
        self.freeze();self.select(1);self.plans.confirm_group_invited('A',1)
        entries=self.plans.entries([self.record],2)
        with patch('pinned_members.require_environment'),patch('pinned_members.open_pinned_groups',return_value={'ok':False}) as native:
            result=select_pinned_member_queue(entries,2,self.path/'run.json')
        native.assert_called_once();self.assertEqual(result['jobs'][0]['source'],'confirmed_batch')
        self.assertEqual(result['jobs'][0]['batch_id'],self.batch)

    def test_gui_reads_current_batch_and_declined_ack_leaves_state(self):
        from app import App
        self.add();self.add();app=App.__new__(App)
        app.group_operation_ready=MagicMock();app.pinned_account_record=MagicMock(return_value=('A',self.record))
        app.current_batch=MagicMock(return_value=self.batch);app.pinned_member_plans=self.plans
        app.load_pinned_member_input=MagicMock();app.refresh_groups=MagicMock();app.status=MagicMock()
        app.root=MagicMock();app.refresh=MagicMock()
        app.make_pinned_batch_plan();self.assertEqual(self.plans.get('A')['batch_id'],self.batch)
        self.select(1)
        with patch('app.messagebox.askyesno',return_value=False):app.confirm_pinned_group_invited(1)
        self.assertEqual(self.plans.states('A')[1]['state'],'waiting_for_manual_invite')
        with patch('app.messagebox.askyesno',return_value=True):app.confirm_pinned_group_invited(1)
        self.assertEqual(self.plans.states('A')[1]['state'],'confirmed_invited')


if __name__=='__main__':unittest.main()
