import copy
import json
import tempfile
import queue
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from batch_plan_preparation import BatchPlanPreparation
from contact_queue import ContactQueue
from groups import GroupCatalog
from pinned_groups import PinnedGroupBindings
from pinned_members import PinnedMemberPlans
from store import Store
from test_contact_queue import window,profile,limit
from test_pinned_groups import report


class BatchPlanPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'db.sqlite3')
        self.catalog=GroupCatalog(self.s);self.bindings=PinnedGroupBindings(self.s)
        self.plans=PinnedMemberPlans(self.s);self.q=ContactQueue(self.s,group_catalog=self.catalog,pinned_groups=self.bindings)
        for i,account in enumerate(('A','B'),1):
            w=window(i);r=report(w,portuguese=i==2);self.bindings.save(account,w,r,copy.deepcopy(r))
        self.s.import_text('phone','\n'.join('+55169912345%02d'%i for i in range(12)))
        self.bulk=BatchPlanPreparation(self.s,self.plans,self.bindings,self.q)

    def tearDown(self):self.s.close();self.tmp.cleanup()

    def start(self,targets=(2,2)):
        qid=self.q.configure([{'account':a,'target':t,'window':window(i)} for i,(a,t) in enumerate(zip(('A','B'),targets),1)])
        self.q.start(qid);return qid

    def add(self,qid,restricted=False):
        j=self.q.current(qid);j=self.q.claim_fill(qid,j['id'])
        self.q.finish_fill(qid,j['id'],{'ok':True,'mode':'fill_only','number':str(j['preview_number']),
            'phone_matches':True,'empty_guard':True,'contact_created':False,'final_invite_clicked':False})
        j=self.q.current(qid);r=limit(j['window']) if restricted else profile(j)
        return self.q.accept(qid,j['id'],[r,copy.deepcopy(r)])

    def done(self):
        qid=self.start()
        for _ in range(4):self.add(qid)
        return qid

    def test_noncontiguous_successes_preview_then_atomic_save_without_renumbering(self):
        batches={a:self.s.create_batch(a,2) for a in ('A','B')}
        for a in ('A','B','A','B'):
            item,_=self.s.reserve_next(batches[a]);self.s.record_add_result(item['id'],'added')
        qid=self.start();self.assertEqual(self.q.snapshot(qid)['state'],'done')
        before=self.s.rows();stats=self.s.addition_stats();p=self.bulk.preview(qid)
        self.assertEqual([r['numbers'] for r in p['rows']],[['1','3'],['2','4']])
        self.assertIsNone(self.plans.get('A'));self.assertIsNone(self.plans.get('B'))
        result=self.bulk.confirm(p)
        self.assertEqual([r['numbers'] for r in result['saved']],[['1','3'],['2','4']])
        self.assertEqual(self.s.rows(),before);self.assertEqual(self.s.addition_stats(),stats)
        self.assertEqual(self.s.next_contact_number(),5)
        self.assertEqual(self.bulk.confirm(self.bulk.preview(qid))['saved'],result['saved'])

    def test_completed_batches_skip_without_replacing_history_or_activating_invites(self):
        self.done();p=self.bulk.preview();self.bulk.confirm(p)
        beforeplans={a:self.plans.get(a) for a in ('A','B')}
        with self.s.db:
            self.s.db.execute("UPDATE batches SET status='completed'")
            self.s.db.execute("UPDATE items SET status='completed' WHERE contact_number IS NOT NULL")
        rows=self.s.rows();p=self.bulk.preview()
        self.assertEqual([r['state'] for r in p['rows']],['completed','completed'])
        self.assertEqual(self.bulk.confirm(p)['saved'],[])
        self.assertEqual(self.s.rows(),rows)
        self.assertEqual({a:self.plans.get(a) for a in ('A','B')},beforeplans)

    def test_restriction_keeps_held_phone_and_only_successes_are_members(self):
        qid=self.start((20,1));self.add(qid);self.add(qid,restricted=True)
        self.assertIsNone(self.q.current(qid))
        self.assertEqual(self.q.snapshot(qid)['jobs'][1]['state'],'queued')
        self.assertEqual(self.s.next_contact_number(),2)
        self.assertEqual(len([r for r in self.s.rows() if r['status']=='uncertain']),1)

    def test_no_list_partial_finishes_only_after_review_and_preserves_actual_numbers(self):
        with self.s.db:self.s.db.execute('DELETE FROM items WHERE seq>2')
        qid=self.start((20,1));self.add(qid);self.add(qid)
        p=self.bulk.preview();self.assertEqual(p['rows'][0]['numbers'],['1','2'])
        self.assertTrue(p['rows'][0]['finish_adding']);self.assertEqual(p['rows'][1]['state'],'empty')
        self.assertEqual(self.s.batch(p['rows'][0]['batch_id'])['status'],'active')
        result=self.bulk.confirm(p);self.assertEqual(len(result['saved']),1)
        self.assertTrue(result['contact_database_updated'])
        self.assertEqual(self.s.batch(p['rows'][0]['batch_id'])['status'],'waiting')
        self.assertEqual(self.s.next_contact_number(),3)

    def test_running_stopped_and_newer_queues_never_generate(self):
        qid=self.start()
        with self.assertRaises(ValueError):self.bulk.preview()
        self.q.stop(qid)
        with self.assertRaises(ValueError):self.bulk.preview()

    def test_changed_success_or_binding_after_preview_prevents_all_saves(self):
        self.done();p=self.bulk.preview();last=p['rows'][1]['members'][-1]['item_id']
        with self.s.db:self.s.db.execute('UPDATE items SET contact_number=90 WHERE id=?',(last,))
        with self.assertRaisesRegex(ValueError,'已变化'):self.bulk.confirm(p)
        self.assertIsNone(self.plans.get('A'));self.assertIsNone(self.plans.get('B'))
        self.assertIsNone(self.bulk.latest())

    def test_late_write_error_rolls_back_every_account_plan_and_audit(self):
        self.done();p=self.bulk.preview();events=self.s.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        real=self.plans._save_batch_record
        def save(record,batch_id,**kw):
            if record['account']=='B':raise RuntimeError('late failure')
            return real(record,batch_id,**kw)
        with patch.object(self.plans,'_save_batch_record',side_effect=save):
            with self.assertRaises(RuntimeError):self.bulk.confirm(p)
        self.assertIsNone(self.plans.get('A'));self.assertIsNone(self.plans.get('B'))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],events)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM pinned_batch_member_plans').fetchone()[0],0)

    def test_queue_binding_change_is_rejected_and_preview_does_not_mutate_items(self):
        self.done();before=self.s.rows()
        row=self.s.db.execute("SELECT observation_json FROM pinned_group_observations WHERE account='B'").fetchone()
        record=json.loads(row[0]);record['targets'][0]['name']='changed'
        with self.s.db:self.s.db.execute("UPDATE pinned_group_observations SET observation_json=? WHERE account='B'",(json.dumps(record),))
        with self.assertRaises(ValueError):self.bulk.preview()
        self.assertEqual(self.s.rows(),before);self.assertIsNone(self.plans.get('A'))

    def test_page_cleanup_takes_database_snapshot_before_real_background_thread(self):
        from app import App
        from test_group_cleanup import cleaned
        self.done();p=self.bulk.preview();self.bulk.confirm(p)
        records=[r['record'] for r in p['rows']];app=App.__new__(App)
        app.store=self.s;app.pinned_member_plans=self.plans;app.group_operation_ready=lambda:None
        app.selected_pinned_observations=lambda **kw:records
        app.root=SimpleNamespace(after=lambda *args:None);app.status=SimpleNamespace(set=lambda text:None)
        app.probe_button=SimpleNamespace(configure=lambda **kw:None);app.probe_queue=queue.Queue()
        threads=[];real_thread=threading.Thread
        def spawn(*args,**kw):
            t=real_thread(*args,**kw);threads.append(t);return t
        def native(w,script,payload):
            self.assertEqual(len(payload['contacts']),2)
            record=next(r for r in records if r['window']['hwnd']==w['hwnd'])
            contact=payload['contacts'][-1]
            return cleaned(record,('close_profile',),'contact',contact)
        with patch('app.DATA',Path(self.tmp.name)),patch('app.threading.Thread',side_effect=spawn),patch('group_cleanup.run_window_script',side_effect=native):
            app.cleanup_selected_groups()
            for t in threads:t.join(2);self.assertFalse(t.is_alive())
        messages=[]
        while not app.probe_queue.empty():messages.append(app.probe_queue.get_nowait())
        result=messages[-1][1]['group_cleanup_result'];self.assertTrue(result['ok'])
        self.assertEqual(self.s.next_contact_number(),5)


    def test_independent_latest_batch_switch_keeps_old_unfinished_history(self):
        qid=self.done();oldpreview=self.bulk.preview(qid);self.bulk.confirm(oldpreview)
        old={a:self.plans.get(a) for a in ('A','B')};oldrows=copy.deepcopy([r for r in self.s.rows() if r['batch_id'] in (old['A']['batch_id'],old['B']['batch_id'])]);before_number=self.s.next_contact_number()
        qid=self.q.configure([{'account':a,'target':2,'window':window(i)} for i,a in enumerate(('A','B'),1)],new_batch=True,independent=True)
        self.q.start(qid)
        for _ in range(4):self.add(qid)
        number=self.s.next_contact_number();p=self.bulk.preview(qid)
        self.assertEqual([r['previous_plan']['batch_id'] for r in p['rows']],[old['A']['batch_id'],old['B']['batch_id']])
        self.bulk.confirm(p)
        self.assertEqual(self.s.next_contact_number(),number)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM batch_plan_switch_history').fetchone()[0],2)
        for a in ('A','B'):
            self.assertNotEqual(self.plans.get(a)['batch_id'],old[a]['batch_id'])
            archived=json.loads(self.s.db.execute('SELECT plan_json FROM pinned_batch_member_plans WHERE batch_id=?',(old[a]['batch_id'],)).fetchone()[0])
            self.assertEqual(archived,old[a]);self.assertNotEqual(self.s.batch(old[a]['batch_id'])['status'],'completed')
        for row in oldrows:self.assertEqual(next(r for r in self.s.rows() if r['id']==row['id']),row)

if __name__=='__main__':unittest.main()
