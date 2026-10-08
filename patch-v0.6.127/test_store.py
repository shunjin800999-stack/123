import tempfile
import unittest
import sqlite3
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

from store import Store, normalize


class QueueTests(unittest.TestCase):
    def test_global_numbers_continue_between_accounts_and_restart(self):
        self.seed();a=self.store.create_batch('A');b=self.store.create_batch('B')
        numbers=[]
        for batch in (a,b,a):
            row,_=self.store.reserve_next(batch);numbers.append(self.store.record_add_result(row['id'],'added'))
        self.assertEqual(numbers,[1,2,3]);self.assertEqual(self.store.batch(b)['next_number'],4)
        self.store.close();self.store=Store(self.path)
        c=self.store.create_batch('C');row,_=self.store.reserve_next(c)
        self.assertEqual(self.store.record_add_result(row['id'],'added'),4)
        self.assertEqual(self.store.addition_stats()['today_added'],4)

    def test_concurrent_connections_allocate_unique_global_numbers(self):
        self.seed();rows=[]
        for account in ('A','B'):
            batch=self.store.create_batch(account);rows.append(self.store.reserve_next(batch)[0])
        barrier=Barrier(2)
        def add(row):
            other=Store(self.path)
            try:
                barrier.wait(timeout=5);return other.record_add_result(row['id'],'added')
            finally:other.close()
        with ThreadPoolExecutor(max_workers=2) as pool:numbers=list(pool.map(add,rows))
        self.assertEqual(sorted(numbers),[1,2]);self.assertEqual(self.store.next_contact_number(),3)

    def test_other_account_consuming_preview_stops_stale_profile_confirmation(self):
        self.seed();a=self.store.create_batch('A');b=self.store.create_batch('B')
        row_a,_=self.store.reserve_next(a);row_b,_=self.store.reserve_next(b)
        self.store.record_add_result(row_b['id'],'added')
        with self.assertRaisesRegex(ValueError,'全局编号'):
            self.store.record_add_result(row_a['id'],'added',expected_phone=row_a['value'],expected_number=1)
        self.assertEqual(self.store.next_contact_number(),2)
        self.assertEqual(next(r for r in self.store.rows() if r['id']==row_a['id'])['status'],'reserved')

    def test_failures_and_calibration_do_not_increase_success_count(self):
        self.seed();a=self.store.create_batch('A');b=self.store.create_batch('B')
        row,_=self.store.reserve_next(a);self.store.record_add_result(row['id'],'ordinary_failure','不存在')
        self.assertEqual(self.store.next_contact_number(),1)
        row,_=self.store.reserve_next(b);self.store.record_add_result(row['id'],'restriction','限频')
        self.assertEqual(self.store.addition_stats()['today_added'],0)
        self.store.set_next_number(21)
        row,_=self.store.reserve_next(a);self.assertEqual(self.store.record_add_result(row['id'],'added'),21)
        self.assertEqual(self.store.addition_stats()['today_added'],1)
        with self.assertRaises(ValueError):self.store.set_next_number(1)
        self.store.close();self.store=Store(self.path)
        self.assertEqual(self.store.next_contact_number(),22)

    def test_add_date_survives_later_invitation_and_midnight_without_reset(self):
        self.seed();a=self.store.create_batch('A',1)
        row,_=self.store.reserve_next(a);self.store.record_add_result(row['id'],'added')
        yesterday=datetime.now().astimezone().replace(hour=12,minute=0,second=0,microsecond=0)-timedelta(days=1)
        stamp=yesterday.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        self.store.db.execute('UPDATE items SET added_at=? WHERE id=?',(stamp,row['id']));self.store.db.commit()
        self.store.mark_waiting(a);self.store.confirm_invited([row['id']])
        self.assertEqual(self.store.addition_stats()['today_added'],0)
        self.assertEqual(self.store.addition_stats(yesterday.date().isoformat())['today_added'],1)
        self.store.close();self.store=Store(self.path)
        self.assertEqual(self.store.next_contact_number(),2)
        self.assertEqual(next(r for r in self.store.rows() if r['id']==row['id'])['added_at'],stamp)

    def test_old_schema_and_duplicate_numbers_migrate_without_renaming(self):
        path=Path(self.tmp.name)/'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript('''
              CREATE TABLE batches(id INTEGER PRIMARY KEY,account TEXT,target INTEGER,mode TEXT DEFAULT 'phone',status TEXT DEFAULT 'active',next_number INTEGER,reason TEXT DEFAULT '',created TEXT);
              CREATE TABLE items(id INTEGER PRIMARY KEY,source TEXT,seq INTEGER,value TEXT,origin TEXT DEFAULT '',origin_line INTEGER DEFAULT 1,status TEXT DEFAULT 'ready',batch_id INTEGER,account TEXT,contact_number INTEGER,reason TEXT DEFAULT '',updated TEXT);
              CREATE TABLE events(id INTEGER PRIMARY KEY,time TEXT,action TEXT,item_id INTEGER,batch_id INTEGER,detail TEXT);
              INSERT INTO batches(id,account,target,next_number) VALUES(1,'A',1,2),(2,'B',1,2);
              INSERT INTO items(id,source,seq,value,status,batch_id,account,contact_number) VALUES(1,'username',1,'@old_a','added',1,'A',1),(2,'username',2,'@old_b','added',2,'B',1),(3,'username',3,'@new','ready',NULL,NULL,NULL);
              INSERT INTO events(time,action,item_id,batch_id,detail) VALUES(strftime('%Y-%m-%d %H:%M:%f','now'),'added',1,1,'');
            ''')
        other=Store(path)
        try:
            backup=path.with_name('legacy-before-global-numbering.sqlite3')
            with sqlite3.connect(backup) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0],3)
                self.assertNotIn('added_at',{row[1] for row in db.execute('PRAGMA table_info(items)')})
            self.assertEqual([r['contact_number'] for r in other.rows()[:2]],[1,1])
            self.assertEqual(other.next_contact_number(),2)
            stats=other.addition_stats();self.assertEqual(stats['today_added'],1);self.assertEqual(stats['unknown_add_date'],1)
            c=other.create_batch('C');row,_=other.reserve_next(c)
            self.assertEqual(other.record_add_result(row['id'],'added'),2)
            self.assertEqual(other.addition_stats()['total_added'],3)
        finally:other.close()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'test.sqlite3'
        self.store=Store(self.path)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def seed(self):
        self.store.import_text('phone','+5516991234567\n+5516991234568\n+5516991234569')
        self.store.import_text('username','@example_one\n@example_two\n@example_three')

    def test_reimport_reports_used_without_reset(self):
        result=self.store.import_text('username','@Example_One\nexample_one\nbad/name')
        self.assertEqual((result['new'],result['duplicate'],len(result['invalid']),result['within_batch']),(1,1,1,1))
        batch=self.store.create_batch('A',20)
        row,_=self.store.reserve_next(batch)
        result=self.store.import_text('username','@EXAMPLE_ONE')
        self.assertEqual(result['duplicates'][0]['status'],'reserved')
        self.assertEqual(result['duplicates'][0]['account'],'A')
        self.assertEqual(self.store.rows()[0]['id'],row['id'])

    def test_different_accounts_never_share_reserved_user(self):
        self.seed()
        a=self.store.create_batch('A');b=self.store.create_batch('B')
        first,_=self.store.reserve_next(a)
        second=Store(self.path)
        try:
            other,_=second.reserve_next(b)
            self.assertNotEqual(first['id'],other['id'])
        finally:second.close()

    def test_interrupted_reservation_is_returned_for_review(self):
        self.seed();batch=self.store.create_batch('A')
        first,new=self.store.reserve_next(batch)
        self.assertTrue(new);self.assertIsNone(first['contact_number'])
        self.store.close();self.store=Store(self.path)
        same,new=self.store.reserve_next(batch)
        self.assertFalse(new);self.assertEqual(same['id'],first['id'])

    def test_phone_failure_switches_immediately(self):
        self.seed();batch=self.store.create_batch('A',2)
        first,_=self.store.reserve_next(batch)
        self.store.record_add_result(first['id'],'ordinary_failure','未找到')
        next_user,_=self.store.reserve_next(batch)
        self.assertEqual(next_user['source'],'username')
        self.assertEqual(self.store.record_add_result(next_user['id'],'added'),1)
        next_user,_=self.store.reserve_next(batch)
        self.assertEqual(self.store.record_add_result(next_user['id'],'added'),2)
        self.assertEqual(self.store.progress('phone')['counts']['ready'],2)
        self.assertIsNone(self.store.reserve_next(batch)[0])

    def test_verified_add_checks_reserved_identity_and_expected_number_atomically(self):
        self.seed();batch=self.store.create_batch('A',2)
        row,_=self.store.reserve_next(batch)
        for phone,number in [('+5516991234568',1),(row['value'],2)]:
            with self.assertRaises(ValueError):
                self.store.record_add_result(row['id'],'added',expected_phone=phone,expected_number=number)
            self.assertEqual(self.store.batch(batch)['next_number'],1)
            self.assertEqual(next(x for x in self.store.rows() if x['id']==row['id'])['status'],'reserved')
        self.assertEqual(self.store.record_add_result(row['id'],'added',expected_phone=row['value'],expected_number=1),1)
        with self.assertRaises(ValueError):
            self.store.record_add_result(row['id'],'added',expected_phone=row['value'],expected_number=1)
        self.assertEqual(self.store.batch(batch)['next_number'],2)

    def test_limit_pauses_without_fallback(self):
        self.seed();batch=self.store.create_batch('A')
        row,_=self.store.reserve_next(batch)
        self.store.record_add_result(row['id'],'restriction','稍后重试')
        self.assertEqual(self.store.batch(batch)['mode'],'phone')
        self.assertEqual(self.store.batch(batch)['status'],'paused')
        with self.assertRaises(ValueError):self.store.reserve_next(batch)
        self.assertEqual(self.store.progress('username')['counts']['ready'],3)

    def test_waiting_is_not_success_and_partial_confirmation_persists(self):
        self.seed();batch=self.store.create_batch('A',2)
        ids=[]
        for _ in range(2):
            row,_=self.store.reserve_next(batch);ids.append(row['id'])
            self.store.record_add_result(row['id'],'added')
        self.store.mark_waiting(batch)
        with self.assertRaises(ValueError):self.store.create_batch('A')
        self.assertEqual(self.store.progress('phone')['counts']['completed'],0)
        self.store.confirm_invited([ids[0]])
        self.assertEqual(self.store.batch(batch)['status'],'waiting')
        self.store.close();self.store=Store(self.path)
        self.assertEqual(self.store.progress('phone')['counts']['pending_invite'],1)
        self.store.confirm_invited([ids[1]])
        next_batch=self.store.create_batch('A',1)
        row,_=self.store.reserve_next(next_batch)
        self.assertEqual(self.store.record_add_result(row['id'],'added'),3)

    def test_invalid_mixed_confirmation_rolls_back_all(self):
        self.seed();batch=self.store.create_batch('A',1)
        row,_=self.store.reserve_next(batch);self.store.record_add_result(row['id'],'added');self.store.mark_waiting(batch)
        ready=next(x for x in self.store.rows() if x['status']=='ready')
        with self.assertRaises(ValueError):self.store.confirm_invited([row['id'],ready['id']])
        self.assertEqual(next(x for x in self.store.rows() if x['id']==row['id'])['status'],'pending_invite')

    def test_pending_batch_cannot_hide_an_unconfirmed_reservation(self):
        self.seed();batch=self.store.create_batch('A')
        row,_=self.store.reserve_next(batch);self.store.record_add_result(row['id'],'added')
        self.store.reserve_next(batch)
        with self.assertRaises(ValueError):self.store.mark_waiting(batch)

    def test_live_backup_keeps_committed_progress(self):
        self.seed();batch=self.store.create_batch('A');self.store.reserve_next(batch)
        path=Path(self.tmp.name)/'backup.sqlite3';self.store.backup(path)
        other=Store(path)
        try:self.assertEqual(other.progress('phone')['counts']['reserved'],1)
        finally:other.close()

    def test_normalization_and_no_cross_source_identity_guess(self):
        self.assertEqual(normalize('+55 (16) 99123-4567','phone'),'+5516991234567')
        self.store.import_text('phone','+5516991234567')
        self.store.import_text('username','@same_person')
        self.assertEqual(len(self.store.rows()),2)

    def test_number_format_update_keeps_old_progress_and_continues_without_reuse(self):
        self.seed();batch=self.store.create_batch('A',2)
        first,_=self.store.reserve_next(batch)
        self.store.record_add_result(first['id'],'added')
        self.store.close();self.store=Store(self.path)
        self.assertEqual(next(x for x in self.store.rows() if x['id']==first['id'])['contact_number'],1)
        self.assertEqual(self.store.batch(batch)['next_number'],2)
        second,_=self.store.reserve_next(batch)
        self.assertNotEqual(second['id'],first['id'])
        self.assertEqual(self.store.record_add_result(second['id'],'added'),2)
        import csv
        path=Path(self.tmp.name)/'numbers.csv';self.store.export_csv(path)
        with path.open(encoding='utf-8-sig',newline='') as source:rows=list(csv.reader(source))
        self.assertEqual([row[6] for row in rows[1:3]],['1','2'])

    def test_csv_preserves_phone_as_text_and_safely_exports_free_text(self):
        import csv
        self.store.import_text('phone','+5516991234567',origin='=bad.txt')
        path=Path(self.tmp.name)/'records.csv'
        self.store.export_csv(path)
        with path.open(encoding='utf-8-sig',newline='') as source:
            rows=list(csv.reader(source))
        self.assertEqual(rows[1][2],"'+5516991234567")
        self.assertEqual(rows[1][3],"'=bad.txt")


if __name__=='__main__':unittest.main(verbosity=2)

class UnusableExportTests(unittest.TestCase):
    def test_export_statuses_source_and_original_values_without_modifying_records(self):
        from store import Store
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'progress.sqlite3')
            store.import_text('phone','\n'.join('+55169912345'+str(n).zfill(2) for n in range(6)))
            store.import_text('username','@missing_user\n@void_user\n@saved_user')
            rows=store.rows()
            statuses=['lookup_failed','failed','void','ready','uncertain','added','username_not_found','void','added']
            with store.db:
                for row,status in zip(rows,statuses):store.db.execute('UPDATE items SET status=? WHERE id=?',(status,row['id']))
            before=store.rows();path=Path(folder)/'export.txt'
            self.assertEqual(store.export_unusable_txt(path),5)
            exported=path.read_text(encoding='utf-8-sig').splitlines()
            self.assertEqual(set(exported),{row['value'] for row,status in zip(rows,statuses) if status in ('lookup_failed','failed','void','username_not_found')})
            self.assertEqual(store.export_unusable_txt(path,'username'),2)
            self.assertEqual(path.read_text(encoding='utf-8-sig').splitlines(),['@missing_user','@void_user'])
            self.assertEqual(store.rows(),before)
            store.close()
