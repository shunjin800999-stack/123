import copy
import unittest

from target_replacement import TargetReplacement
import test_batch_plan_preparation as batch_fixture
from test_pinned_members import selected


class TargetReplacementTests(unittest.TestCase):
    def setUp(self):
        self.f=batch_fixture.BatchPlanPreparationTests();self.f.setUp()
        self.s=self.f.s;self.plans=self.f.plans;self.bindings=self.f.bindings
        # Keep deliberately interleaved global numbers as real saved successes.
        batches={a:self.s.create_batch(a,2) for a in ('A','B')}
        for a in ('A','B','A','B'):
            item,_=self.s.reserve_next(batches[a]);self.s.record_add_result(item['id'],'added')
        self.qid=self.f.start();self.f.bulk.confirm(self.f.bulk.preview())
        self.old=[self.bindings.get(a) for a in ('A','B')]
        for r in self.old:self.select(r,1)
        self.plans.confirm_group_invited('A',1)
        self.new=copy.deepcopy(self.old)
        for r in self.new:
            r.pop('saved',None)
            for i,t in enumerate(r['targets']):t.update(name=('666加油','888坚持')[i],runtime_id=t['runtime_id']+'new')
        self.service=TargetReplacement(self.s,self.bindings,self.plans)

    def tearDown(self):self.f.tearDown()

    def select(self,record,slot):
        p=self.plans.get(record['account'])
        job={'account':record['account'],'plan_id':p['plan_id'],'numbers':p['numbers'],
            'slot':slot,'window':record['window'],'state':'waiting_for_manual_invite',
            'selection':selected(record,slot,p['numbers'])}
        self.plans.save_run({'scope':'pinned_member_selection_queue','jobs':[job],
            'final_invite_clicked':False,'contact_database_updated':False})

    def test_atomic_retarget_preserves_contacts_numbers_queue_and_full_old_proofs(self):
        before=self.s.rows();queue=self.f.q.snapshot(self.qid);stats=self.s.addition_stats()
        p=self.service.preview(self.new);oldstates={a:self.plans.states(a) for a in ('A','B')}
        oldplans={a:self.plans.get(a) for a in ('A','B')}
        self.assertEqual([r['numbers'] for r in p['rows']],[['1','3'],['2','4']])
        self.assertEqual(self.s.rows(),before)
        saved=self.service.confirm(p,self.new)
        self.assertEqual(saved['state'],'saved');self.assertFalse(saved['contact_database_updated'])
        self.assertEqual(self.s.rows(),before);self.assertEqual(self.s.addition_stats(),stats)
        self.assertEqual(self.s.next_contact_number(),5);self.assertEqual(self.f.q.snapshot(self.qid),queue)
        for row in saved['rows']:
            a=row['account'];plan=self.plans.get(a)
            self.assertEqual(row['old_plan'],oldplans[a]);self.assertEqual(row['old_selection_states'],oldstates[a])
            self.assertNotEqual(plan['plan_id'],oldplans[a]['plan_id'])
            self.assertEqual(plan['members'],oldplans[a]['members']);self.assertEqual(self.plans.states(a),{})
            self.plans.validate_batch_plan(plan)
        self.assertEqual(len(self.plans.entries(self.new,1)),2)
        self.assertEqual(len(self.plans.entries(self.new,2)),2)
        with self.assertRaises(ValueError):self.plans.confirm_group_invited('A',1)
        self.assertIsNotNone(self.plans.latest())
        self.assertEqual(self.service.latest()['saved'],saved['saved'])
        with self.assertRaises(ValueError):self.service.confirm(p,self.new)
        self.assertEqual(self.s.rows(),before)

    def test_ordinary_save_still_blocks_unfinished_replacement(self):
        with self.assertRaisesRegex(ValueError,'未完成'):self.bindings.save_many(self.new)
        self.assertEqual([self.bindings.get(a) for a in ('A','B')],self.old)

    def test_live_name_order_runtime_or_window_change_prevents_every_write(self):
        p=self.service.preview(self.new)
        variants=[]
        for field,value in (('name','changed'),('runtime_id','changed')):
            v=copy.deepcopy(self.new);v[1]['targets'][0][field]=value;variants.append(v)
        v=copy.deepcopy(self.new);v[1]['window']['hwnd']+=100;variants.append(v)
        variants.append(list(reversed(self.new)))
        for v in variants:
            with self.assertRaises(ValueError):self.service.confirm(p,v)
            self.assertEqual([self.bindings.get(a) for a in ('A','B')],self.old)
            self.assertIsNone(self.service.latest())

    def test_late_plan_write_failure_rolls_back_both_bindings_plans_states_and_audit(self):
        p=self.service.preview(self.new);events=self.s.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        oldplans={a:self.plans.get(a) for a in ('A','B')};oldstates={a:self.plans.states(a) for a in ('A','B')}
        self.s.db.execute('''CREATE TEMP TRIGGER fail_retarget BEFORE UPDATE ON pinned_member_plans
            WHEN NEW.account='B' BEGIN SELECT RAISE(ABORT,'late failure'); END''')
        self.s.db.commit()
        with self.assertRaises(Exception):self.service.confirm(p,self.new)
        self.assertEqual([self.bindings.get(a) for a in ('A','B')],self.old)
        self.assertEqual({a:self.plans.get(a) for a in ('A','B')},oldplans)
        self.assertEqual({a:self.plans.states(a) for a in ('A','B')},oldstates)
        self.assertIsNone(self.service.latest());self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],events)

    def test_changed_confirmation_after_preview_requires_new_review(self):
        p=self.service.preview(self.new);self.plans.confirm_group_invited('B',1)
        with self.assertRaisesRegex(ValueError,'已变化'):self.service.confirm(p,self.new)
        self.assertEqual([self.bindings.get(a) for a in ('A','B')],self.old)

    def test_finished_batch_or_wrong_account_or_window_cannot_be_reopened(self):
        for mutate in ('finished','account','window'):
            with self.s.db:
                if mutate=='finished':self.s.db.execute("UPDATE batches SET status='completed' WHERE account='B'")
            v=copy.deepcopy(self.new)
            if mutate=='account':v[1]['account']='C'
            if mutate=='window':v[1]['window']['pid']+=100
            with self.assertRaises(ValueError):self.service.preview(v)
            with self.s.db:self.s.db.execute("UPDATE batches SET status='active' WHERE account='B'")

    def test_new_group_confirmations_can_finish_same_contacts_only_after_new_selection(self):
        self.service.confirm(self.service.preview(self.new),self.new)
        for slot in (1,2):
            for record in self.new:
                self.select(record,slot);self.plans.confirm_group_invited(record['account'],slot)
        self.assertTrue(all(self.s.batch(self.plans.get(a)['batch_id'])['status']=='completed' for a in ('A','B')))
        self.assertEqual(self.s.next_contact_number(),5)
        self.assertEqual([r['contact_number'] for r in self.s.rows() if r['contact_number']], [1,2,3,4])


if __name__=='__main__':unittest.main()
