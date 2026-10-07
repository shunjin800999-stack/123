import json,tempfile,unittest
from pathlib import Path
from store import Store
from contact_queue import ContactQueue
from test_contact_queue import window


class UsernameQueueHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite3'
        self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.store.import_text('phone','+5516991234567')
        self.store.import_text('username','@test_user\n@second_user')
        self.store.set_next_number(254)

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def configure(self,**kwargs):
        return self.queue.configure([{'account':'账号2','target':1,'window':window(1)}],
            new_batch=True,independent=True,auto_submit=True,**kwargs)

    def hold(self,status='uncertain',batch_status='review'):
        bid=self.store.create_batch('账号2');item,_=self.store.reserve_next(bid,phone_only=True)
        with self.store.db:
            self.store.db.execute('UPDATE items SET seq=265,status=?,reason=? WHERE id=?',(status,'原手机号结果未确认',item['id']))
            self.store.db.execute('UPDATE batches SET status=? WHERE id=?',(batch_status,bid))
        item=dict(self.store.db.execute('SELECT * FROM items WHERE id=?',(item['id'],)).fetchone())
        return bid,item

    def test_old_phone_hold_never_blocks_or_changes_in_username_queue(self):
        for item_status,batch_status in [('reserved','active'),('uncertain','review'),('uncertain','paused')]:
            with self.subTest(item_status=item_status,batch_status=batch_status):
                # Each case uses a separate account database.
                with tempfile.TemporaryDirectory() as tmp:
                    s=Store(Path(tmp)/'db');q=ContactQueue(s)
                    s.import_text('phone','+5516991234567');s.import_text('username','@test_user')
                    s.set_next_number(254);bid=s.create_batch('账号2');held,_=s.reserve_next(bid,phone_only=True)
                    with s.db:
                        s.db.execute('UPDATE items SET seq=265,status=? WHERE id=?',(item_status,held['id']))
                        s.db.execute('UPDATE batches SET status=? WHERE id=?',(batch_status,bid))
                    original=dict(s.db.execute('SELECT * FROM items WHERE id=?',(held['id'],)).fetchone())
                    qid=q.configure([{'account':'账号2','target':1,'window':window(1)}],new_batch=True,independent=True,auto_submit=True,contact_source='username')
                    job=q.start(qid)
                    self.assertEqual(job['item']['source'],'username')
                    self.assertEqual(job['item']['seq'],1)
                    self.assertEqual(s.next_contact_number(),254)
                    self.assertEqual(s.batch(bid)['status'],'archived')
                    self.assertEqual(dict(s.db.execute('SELECT * FROM items WHERE id=?',(held['id'],)).fetchone()),original)
                    event=s.db.execute("SELECT detail FROM events WHERE action='batch_archived_for_username_queue'").fetchone()
                    self.assertEqual(json.loads(event[0])['previous_status'],batch_status)
                    s.close()

    def test_clear_old_queue_keeps_phone_hold_but_allows_username_queue(self):
        old=self.configure();self.queue.start(old);held=self.queue.current(old)['item']
        self.queue.stop(old);self.queue.clear()
        new=self.configure(contact_source='username');job=self.queue.start(new)
        self.assertEqual(job['item']['source'],'username')
        self.assertEqual(next(r for r in self.store.rows() if r['id']==held['id'])['status'],'reserved')
        self.assertEqual(self.store.next_contact_number(),254)

    def test_username_hold_still_blocks_after_clear_and_with_later_start(self):
        old=self.configure(contact_source='username');self.queue.start(old)
        self.queue.stop(old);self.queue.clear()
        with self.assertRaisesRegex(ValueError,'未确认用户名.*名单序号1'):
            self.configure(contact_source='username',start_username_seq=2)
        self.assertEqual(self.store.next_contact_number(),254)

    def test_phone_queue_still_blocks_old_hold_unless_explicit_266(self):
        bid,item=self.hold()
        with self.assertRaises(ValueError):self.configure()
        qid=self.configure(start_phone_seq=266)
        self.assertIsNone(self.queue.start(qid))
        self.assertEqual(next(r for r in self.store.rows() if r['id']==item['id']),item)

    def test_switch_source_keeps_successful_phone_contact_and_invite_state(self):
        bid=self.store.create_batch('账号2');item,_=self.store.reserve_next(bid,phone_only=True)
        self.store.record_add_result(item['id'],'added')
        original=dict(self.store.db.execute('SELECT * FROM items WHERE id=?',(item['id'],)).fetchone())
        qid=self.configure(contact_source='username');self.queue.start(qid)
        self.assertEqual(self.store.batch(bid)['status'],'waiting')
        self.assertEqual(dict(self.store.db.execute('SELECT * FROM items WHERE id=?',(item['id'],)).fetchone()),original)
        self.assertEqual(self.store.next_contact_number(),255)
