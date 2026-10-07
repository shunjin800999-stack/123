import copy
import json
import tempfile
import unittest
from pathlib import Path

from groups import GroupCatalog
from pinned_groups import PinnedGroupBindings, consistent_pair
from pinned_members import PinnedMemberPlans
from pinned_scan import scan_pinned_windows, recheck_pinned_preview
from store import Store
from test_contact_queue import window
from test_pinned_groups import report


def batch_report(w):
    r=report(w)
    r.update(ordinary_chat_verified=True,search_empty=True,first_two_visible=True)
    return r


def read(w,script,payload):
    if script!='inspect_groups.ps1' or payload!={'batch_scan':True}:
        raise AssertionError('Batch discovery must use the guarded read-only probe')
    return batch_report(w)


class BatchPinnedScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'db.sqlite3');self.catalog=GroupCatalog(self.store)
        self.pins=PinnedGroupBindings(self.store);self.members=PinnedMemberPlans(self.store)
        for n,account in ((1,'账号A'),(2,'账号B')):
            r=report(window(n));self.pins.save(account,window(n),r,r)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def known(self):
        return [{'account':a,'window':self.pins.get(a)['window']} for a in self.pins.accounts()]

    def preview(self,windows=None,reader=read):
        return scan_pinned_windows(windows or [window(1),window(2)],self.known(),reader=reader,pause=lambda _:None)

    def test_prepare_each_window_before_reading_without_saving_bindings(self):
        calls=[]
        def prepare(w):calls.append(('prepare',w['hwnd']));return {'state':'ready'}
        def reader(w,script,payload):calls.append(('read',w['hwnd']));return read(w,script,payload)
        result=scan_pinned_windows([window(1)],self.known(),reader=reader,pause=lambda _:None,prepare=prepare)
        self.assertTrue(result['ok']);self.assertFalse(result['read_only'])
        self.assertEqual([kind for kind,hwnd in calls],['prepare','read','read'])
        self.assertEqual(result['saved_accounts'],[])

    def test_failed_preparation_does_not_probe_or_overwrite_binding(self):
        def prepare(w):raise ValueError('弹窗未关闭')
        result=scan_pinned_windows([window(1)],self.known(),reader=lambda *args:self.fail('must not read'),pause=lambda _:None,prepare=prepare)
        self.assertEqual(result['jobs'][0]['state'],'review')
        self.assertIn('弹窗未关闭',result['jobs'][0]['reason'])

    def recheck(self,preview,indices=(1,2),reader=read):
        return recheck_pinned_preview(preview,indices,reader=reader,pause=lambda _:None)

    def snapshot(self):
        return {'pins':[self.pins.get(a) for a in self.pins.accounts()],
            'events':[dict(r) for r in self.store.db.execute('SELECT * FROM events')],
            'rows':self.store.rows(),'stats':self.store.addition_stats()}

    def test_one_scan_is_read_only_and_same_named_groups_remain_window_bound(self):
        before=self.snapshot();preview=self.preview()
        self.assertTrue(preview['ok']);self.assertEqual(preview['saved_accounts'],[])
        self.assertEqual([j['account'] for j in preview['jobs']],['账号A','账号B'])
        self.assertEqual([j['observation']['targets'][0]['name'] for j in preview['jobs']],['你好123','你好123'])
        self.assertNotEqual(preview['jobs'][0]['observation']['targets'][0]['runtime_id'],preview['jobs'][1]['observation']['targets'][0]['runtime_id'])
        self.assertNotIn('preview',json.dumps([j['observation'] for j in preview['jobs']]))
        self.assertEqual(before,self.snapshot())

    def test_failed_window_does_not_hide_successful_windows_or_modify_old_binding(self):
        before=self.snapshot()
        def failure(w,*args):
            if w['hwnd']==101:raise RuntimeError('Close New Contact first')
            return read(w,*args)
        preview=self.preview(reader=failure)
        self.assertFalse(preview['ok'])
        self.assertEqual([j['state'] for j in preview['jobs']],['review','ready'])
        with self.assertRaises(ValueError):self.recheck(preview)
        records=self.recheck(preview,(2,))
        self.assertEqual(records[0]['account'],'账号B')
        self.assertEqual(before,self.snapshot())

    def test_case_insensitive_path_preserves_old_label_after_window_restart(self):
        w=window(1);w.update(hwnd=501,pid=601,path=w['path'].lower(),title='Different chat title')
        preview=self.preview([w])
        self.assertTrue(preview['ok']);self.assertEqual(preview['jobs'][0]['account'],'账号A')
        self.assertTrue(preview['jobs'][0]['existing_account'])

    def test_new_labels_are_unique_and_rescan_reuses_confirmed_name(self):
        preview=self.preview([window(3),window(4)])
        self.assertEqual([j['account'] for j in preview['jobs']],['账号1','账号2'])
        preview['jobs'][0]['account']='新账号甲'
        self.pins.save_many(self.recheck(preview))
        again=self.preview([window(4),window(3)])
        self.assertEqual([j['account'] for j in again['jobs']],['账号2','新账号甲'])

    def test_minimized_or_multiple_same_executable_windows_are_visible_failures(self):
        minimized=window(1);minimized['minimized']=True
        calls=[]
        def reader(*args):calls.append(args);return read(*args)
        preview=self.preview([minimized,window(2)],reader)
        self.assertEqual([j['state'] for j in preview['jobs']],['review','ready'])
        self.assertEqual(len(calls),2)
        duplicate=window(1);duplicate.update(hwnd=501,pid=601)
        calls.clear();preview=self.preview([window(1),duplicate],reader)
        self.assertEqual([j['state'] for j in preview['jobs']],['review','review'])
        self.assertEqual(calls,[])

    def test_missing_ordinary_list_or_search_proof_is_not_a_valid_batch_row(self):
        for key in ('ordinary_chat_verified','search_empty','first_two_visible'):
            def bad(w,*_):
                r=batch_report(w);r[key]=False;return r
            self.assertEqual(self.preview([window(1)],bad)['jobs'][0]['state'],'review')

    def test_two_live_reads_must_match_and_belong_to_exact_window(self):
        count=0
        def changed(w,*_):
            nonlocal count
            count+=1;r=batch_report(w)
            if count==2:r['candidates'][0]['name']='Group, Wrong target, Pinned'
            return r
        self.assertEqual(self.preview([window(1)],changed)['jobs'][0]['state'],'review')
        def wrong_window(w,*_):return batch_report(window(99))
        self.assertEqual(self.preview([window(1)],wrong_window)['jobs'][0]['state'],'review')

    def test_target_changed_after_human_review_rejects_selected_save_without_writes(self):
        before=self.snapshot();preview=self.preview()
        def changed(w,*args):
            r=read(w,*args)
            if w['hwnd']==102:r['candidates'][0]['name']='Group, New group, Pinned'
            return r
        with self.assertRaisesRegex(ValueError,'全部未保存'):self.recheck(preview,reader=changed)
        self.assertEqual(before,self.snapshot())

    def test_one_confirmation_saves_all_bindings_without_touching_contacts_or_invites(self):
        self.store.import_text('phone','+5516991234567')
        batch=self.store.create_batch('账号A',1)
        self.store.reserve_next(batch)
        before=self.store.addition_stats();rows=self.store.rows()
        records=self.recheck(self.preview());saved=self.pins.save_many(records)
        self.assertEqual([r['account'] for r in saved],['账号A','账号B'])
        self.assertEqual(before,self.store.addition_stats());self.assertEqual(rows,self.store.rows())
        self.assertEqual(self.members.states('账号A'),{})

    def test_late_conflict_rolls_back_earlier_binding_and_events(self):
        before=self.snapshot();records=self.recheck(self.preview([window(3),window(2)]))
        records[1]['account']='错误别名'
        with self.assertRaises(ValueError):self.pins.save_many(records)
        self.assertEqual(before,self.snapshot());self.assertIsNone(self.pins.get('账号01'))

    def test_unfinished_batch_blocks_changed_binding_but_identical_refresh_is_allowed(self):
        self.store.create_batch('账号A',1)
        self.pins.save_many(self.recheck(self.preview()))
        before=self.snapshot();records=self.recheck(self.preview())
        records.reverse();records[1]['targets'][0]['runtime_id']='changed'
        with self.assertRaisesRegex(ValueError,'未完成批次'):self.pins.save_many(records)
        self.assertEqual(before,self.snapshot())

    def test_unfinished_manual_member_plan_also_protects_old_targets(self):
        self.members.save(self.pins.get('账号A'),['1','2'])
        records=self.recheck(self.preview());records[0]['window']['hwnd']=999
        with self.assertRaisesRegex(ValueError,'未完成批次'):self.pins.save_many(records)
        self.assertEqual(self.members.get('账号A')['numbers'],['1','2'])

    def test_completed_batches_allow_new_window_without_rewriting_historical_plans(self):
        batch=self.store.create_batch('账号A',1)
        self.store.db.execute("UPDATE batches SET status='completed' WHERE id=?",(batch,));self.store.db.commit()
        old=self.pins.get('账号A');w=window(1);w.update(hwnd=801,pid=901)
        records=self.recheck(self.preview([w]),(1,));self.pins.save_many(records)
        self.assertEqual(self.pins.get('账号A')['window']['hwnd'],801)
        self.assertEqual(old['window']['hwnd'],101);self.assertEqual(self.store.next_contact_number(),1)

    def test_selection_and_size_guards(self):
        preview=self.preview()
        for indices in ([],[1,1],[True],[99]):
            with self.assertRaises(ValueError):self.recheck(preview,indices)
        with self.assertRaises(ValueError):self.preview([window(i) for i in range(1,82)])
        preview['jobs'][1]['account']=preview['jobs'][0]['account']
        with self.assertRaises(ValueError):self.recheck(preview)


if __name__=='__main__':unittest.main()
