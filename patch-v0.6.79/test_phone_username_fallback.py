import tempfile,unittest
from pathlib import Path
from store import Store
from contact_queue import ContactQueue
from test_contact_queue import window
import test_unregistered_contact as phone
from test_unregistered_contact import result,dismissed
import test_contact_submit as submit
from test_username_missing import missing_report

class PhoneUsernameFallbackTests(unittest.TestCase):
    fill=submit.ContactSubmitTests.fill
    submission=phone.UnregisteredTests.submission
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite3'
        self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.store.import_text('phone','+5516991234501\n+5516991234502\n+5516991234503')
        self.store.import_text('username','@missing_one\n@missing_two\n@third_user')
        self.store.set_next_number(12)
        self.qid=self.queue.configure([{'account':a,'target':3,'window':window(i)} for i,a in enumerate(('A','B'),1)],
            auto_submit=True,new_batch=True,independent=True,phone_username_fallback=True)
        self.queue.start(self.qid)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def fail_phone(self):
        j=self.submission();r=result(j)
        self.queue.claim_unregistered(self.qid,j['id'],[r,r])
        self.queue.save_unregistered_result(self.qid,j['id'],j['item_id'],dismissed(j))
        self.queue.finish_unregistered(self.qid,j['id'],j['item_id'])
    def test_phone_twice_switches_same_account_then_username_twice_next_account(self):
        self.fail_phone();self.assertEqual(self.queue.current(self.qid)['item']['source'],'phone')
        self.fail_phone();j=self.queue.current(self.qid)
        self.assertEqual(j['account'],'A');self.assertEqual(j['active_source'],'username')
        self.assertEqual(j['item']['value'],'@missing_one')
        for value in ('@missing_one','@missing_two'):
            j=self.queue.current(self.qid);self.assertEqual(j['item']['value'],value)
            j=self.queue.claim_open(self.qid,j['id'])
            self.queue.save_open_result(self.qid,j['id'],j['item_id'],missing_report(j))
            self.queue.finish_open(self.qid,j['id'],j['item_id'])
        j=self.queue.current(self.qid)
        self.assertEqual(j['account'],'B');self.assertEqual(j['item']['source'],'phone')
        self.assertEqual(j['lookup_failures'],0);self.assertEqual(j['username_failures'],0)
        self.assertEqual(self.store.next_contact_number(),12)
        self.assertEqual(self.queue.snapshot(self.qid)['jobs'][0]['state'],'paused')
    def test_switch_source_persists_on_restart(self):
        self.fail_phone();self.fail_phone()
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
        j=self.queue.snapshot(self.qid)['jobs'][0]
        self.assertEqual(j['active_source'],'username');self.assertEqual(j['item']['value'],'@missing_one')
        self.assertIsNone(self.queue.current(self.qid))

    def test_cleanup_uses_original_failed_phone_form_only(self):
        self.fail_phone();self.fail_phone()
        j=self.queue.current(self.qid);proof=self.queue.username_phone_form_cleanup(j)
        self.assertTrue(proof['main_runtime_id']);self.assertTrue(proof['contact_runtime_id'])
        with self.store.db:self.store.db.execute('UPDATE contact_queues SET phone_username_fallback=0 WHERE id=?',(self.qid,))
        self.assertIsNone(self.queue.username_phone_form_cleanup(j))

    def test_resume_username_blocked_by_phone_form_keeps_item_and_number(self):
        import test_username_contact as fixtures
        self.fail_phone();self.fail_phone();j=self.queue.current(self.qid)
        j=self.queue.claim_open(self.qid,j['id']);r=fixtures.opened(j)
        r.update(ok=False,stage='initial',state='review',add_invoked=False,
            errors=['Existing dialog preserved; return to the main chat first.'])
        self.queue.save_open_result(self.qid,j['id'],j['item_id'],r,'blocked')
        self.queue.stop(self.qid,'blocked',review=True)
        recovered=self.queue.restart_unsubmitted_username(self.qid,j['window'])
        self.assertEqual(recovered['item_id'],j['item_id'])
        self.assertEqual(recovered['state'],'waiting_form');self.assertEqual(self.store.next_contact_number(),12)
