import tempfile
import unittest
from pathlib import Path
from contact_queue import ContactQueue
from store import Store
from test_contact_queue import window, profile
from test_contact_submit import filled, submitted
from test_unregistered_contact import result, dismissed


class StartPhoneSequenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'progress.sqlite3'
        self.store=Store(self.path)
        self.store.import_text('phone','\n'.join(str(5516991000000+i).join(['+','']) for i in range(1,271)))
        self.store.set_next_number(251)
        self.queue=ContactQueue(self.store)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def old_hold(self,seq=265):
        batch=self.store.create_batch('账号2')
        item,_=self.store.reserve_next(batch,start_phone_seq=seq)
        self.store.record_add_result(item['id'],'uncertain','旧结果待核查')
        return self.store.batch(batch),dict(self.store.db.execute('SELECT * FROM items WHERE id=?',(item['id'],)).fetchone())

    def configure(self,seq=266):
        return self.queue.configure([{'account':a,'target':3,'window':window(i)} for i,a in enumerate(('账号2','账号3'),1)],
            new_batch=True,independent=True,auto_submit=True,start_phone_seq=seq)

    def submit(self,qid):
        j=self.queue.current(qid);j=self.queue.claim_fill(qid,j['id'])
        self.queue.finish_fill(qid,j['id'],filled(j));j=self.queue.claim_submit(qid,j['id'])
        return j

    def fail(self,qid):
        j=self.submit(qid)
        self.queue.save_submit_result(qid,j['id'],j['item_id'],submitted(j,dialog=True))
        self.queue.finish_submit(qid,j['id'],j['item_id']);r=result(j)
        self.queue.claim_unregistered(qid,j['id'],[r,r])
        self.queue.save_unregistered_result(qid,j['id'],j['item_id'],dismissed(j))
        self.queue.finish_unregistered(qid,j['id'],j['item_id'])
        return j

    def test_266_ignores_old_hold_preserves_items_and_global_number(self):
        batch,old=self.old_hold();qid=self.configure();j=self.queue.start(qid)
        self.assertEqual(j['item']['seq'],266)
        self.assertEqual(self.store.next_contact_number(),251)
        self.assertEqual(self.store.batch(batch['id'])['status'],'archived')
        self.assertEqual(dict(self.store.db.execute('SELECT * FROM items WHERE id=?',(old['id'],)).fetchone()),old)
        self.assertTrue(all(r['status']=='ready' for r in self.store.rows() if r['seq']<265))
        self.assertEqual(self.queue.snapshot(qid)['start_phone_seq'],266)
        self.assertNotEqual(j['batch_id'],batch['id'])

    def test_newer_uncertain_result_still_blocks_and_rolls_back(self):
        batch,old=self.old_hold(266)
        with self.assertRaises(ValueError):self.configure()
        self.assertEqual(self.store.batch(batch['id']),batch)
        self.assertIsNone(self.queue.latest())
        self.assertEqual(dict(self.store.db.execute('SELECT * FROM items WHERE id=?',(old['id'],)).fetchone()),old)

    def test_default_start_still_blocks_old_uncertain_result(self):
        self.old_hold()
        with self.assertRaises(ValueError):self.configure(1)

    def test_two_failures_switch_from_266_to_next_account_at_268(self):
        self.old_hold();qid=self.configure();self.queue.start(qid)
        self.assertEqual(self.fail(qid)['item']['seq'],266)
        self.assertEqual(self.queue.current(qid)['account'],'账号2')
        self.assertEqual(self.fail(qid)['item']['seq'],267)
        j=self.queue.current(qid)
        self.assertEqual((j['account'],j['item']['seq']),('账号3',268))
        self.assertEqual(self.store.next_contact_number(),251)

    def test_success_advances_number_without_returning_to_old_ready_rows(self):
        self.old_hold();qid=self.configure();self.queue.start(qid);j=self.submit(qid)
        self.queue.save_submit_result(qid,j['id'],j['item_id'],submitted(j))
        self.queue.finish_submit(qid,j['id'],j['item_id']);r=profile(j)
        self.queue.accept(qid,j['id'],[r,r])
        self.assertEqual(self.store.next_contact_number(),252)
        self.assertEqual(self.queue.current(qid)['item']['seq'],267)

    def test_saved_start_survives_reopen_and_never_auto_resumes(self):
        self.old_hold();qid=self.configure();self.queue.start(qid)
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.assertEqual(self.queue.snapshot(qid)['start_phone_seq'],266)
        self.assertEqual(self.queue.snapshot(qid)['jobs'][0]['item']['seq'],266)
        self.assertIsNone(self.queue.current(qid))

    def test_no_eligible_rows_finishes_without_reusing_older_rows(self):
        self.old_hold();qid=self.configure(271)
        self.assertIsNone(self.queue.start(qid))
        self.assertEqual(self.queue.snapshot(qid)['state'],'done')
        self.assertEqual(self.store.next_contact_number(),251)

    def test_invalid_start_and_unsupported_modes_rejected(self):
        for value in (0,-1,True,'266'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.configure(value)
        with self.assertRaises(ValueError):
            self.queue.configure([{'account':'账号2','target':1,'window':window(1)}],start_phone_seq=266)
