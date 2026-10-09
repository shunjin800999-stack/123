import copy,json,unittest
from pathlib import Path
from unittest.mock import patch
from username_contact import missing_username_evidence,missing_username_text,missing_result_verified
from store import Store
from contact_queue import ContactQueue
from test_contact_queue import window
import test_username_contact as fixtures


def missing_report(job,mode='open',dialog='missing-modal'):
    r=fixtures.opened(job)
    r.update(mode=mode,state='username_not_found',stage='verified',add_invoked=False,
        field_schema_verified=False,username_verified=False,missing_dialog_runtime_id=dialog,
        not_found_text=missing_username_text(job['item']['value']),not_found_verified=True,not_found_reads=2,
        dismiss_runtime_id='ok-button',dismiss_attempted=True,dismiss_invoked=True,result_absent=True)
    return r


def missing_reads(job):
    r=json.loads((Path(__file__).parent/'test_fixtures'/'username-missing.json').read_text())
    r['process_id']=job['window']['pid'];r['window_handle']=job['window']['hwnd']
    next(c for c in r['controls'] if c['type']=='Text')['name']=missing_username_text(job['item']['value'])
    return [r,copy.deepcopy(r)]


class UsernameMissingTests(unittest.TestCase):
    setUp=fixtures.UsernameContactTests.setUp
    tearDown=fixtures.UsernameContactTests.tearDown
    app=fixtures.UsernameContactTests.app

    def opening(self):
        j=self.queue.current(self.qid)
        return self.queue.claim_open(self.qid,j['id'])

    def stopped_opening(self):
        j=self.opening();r=fixtures.opened(j)
        r.update(ok=False,state='review',stage='resolve_requested',add_invoked=False,
            username_verified=False,field_schema_verified=False,errors=['Resolution produced a dialog'])
        self.queue.save_open_result(self.qid,j['id'],j['item_id'],r,'操作未完成')
        self.queue.stop(self.qid,'操作未完成',review=True)
        return j

    def test_auto_missing_advances_to_next_username_without_allocating_number(self):
        j=self.opening();r=missing_report(j)
        self.assertTrue(missing_result_verified(r,j))
        self.queue.save_open_result(self.qid,j['id'],j['item_id'],r)
        self.assertEqual(self.queue.finish_open(self.qid,j['id'],j['item_id']),'username_not_found')
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        self.assertEqual(self.store.next_contact_number(),254)
        item=next(x for x in self.store.rows() if x['id']==j['item_id'])
        self.assertEqual(item['status'],'username_not_found');self.assertIsNone(item['contact_number'])
        self.assertEqual(self.queue.snapshot(self.qid)['jobs'][0]['lookup_failures'],0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM contact_queue_submissions').fetchone()[0],0)

    def test_all_missing_finishes_no_list_instead_of_stopping_after_two(self):
        for username in ('@test_user','@next_user','@last_user'):
            j=self.opening();self.assertEqual(j['item']['value'],username)
            self.queue.save_open_result(self.qid,j['id'],j['item_id'],missing_report(j))
            self.queue.finish_open(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'done')
        self.assertEqual(self.queue.snapshot(self.qid)['jobs'][0]['state'],'no_list')
        self.assertEqual(self.store.next_contact_number(),254)

    def test_replay_actual_missing_dialog_and_reject_other_user_or_vague_limit(self):
        j=self.queue.current(self.qid);reads=missing_reads(j)
        self.assertIsNotNone(missing_username_evidence(reads,j['window'],j['item']['value']))
        for change in ('username','limit','button','edit','class','identity','window','truncated','duplicate'):
            r=copy.deepcopy(reads)
            if change=='username':next(c for c in r[0]['controls'] if c['type']=='Text')['name']=missing_username_text('@other_user')
            if change=='limit':next(c for c in r[0]['controls'] if c['type']=='Text')['name']='Too many tries. Please try again later.'
            if change=='button':next(c for c in r[0]['controls'] if c['type']=='Button')['name']='Cancel'
            if change=='edit':r[0]['controls'].append({'type':'Edit','visible':True})
            if change=='class':r[0]['scope_class']='class MainWindow'
            if change=='identity':r[1]['scope_runtime_id']='other-modal'
            if change=='window':r[0]['process_id']+=1
            if change=='truncated':r[0]['truncated']=True
            if change=='duplicate':r[0]['controls'].append(copy.deepcopy(next(c for c in r[0]['controls'] if c['type']=='Text')))
            with self.subTest(change=change):self.assertIsNone(missing_username_evidence(r,j['window'],j['item']['value']))

    def test_failed_dismissal_or_changed_username_never_releases_or_numbers(self):
        j=self.opening()
        for key,value in [('result_absent',False),('dismiss_invoked',False),('username','@other_user'),('fields_written',True),('create_attempted',True)]:
            r=missing_report(j);r[key]=value
            with self.subTest(key=key):self.assertFalse(missing_result_verified(r,j))
        r=missing_report(j);r['result_absent']=False
        self.queue.save_open_result(self.qid,j['id'],j['item_id'],r)
        with self.assertRaises(ValueError):self.queue.finish_open(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(next(x for x in self.store.rows() if x['id']==j['item_id'])['status'],'reserved')

    def test_explicit_recovery_dismisses_old_task_without_relaunch_or_create(self):
        j=self.stopped_opening();reads=missing_reads(j)
        claimed,payload=self.queue.claim_username_missing(self.qid,j['id'],reads)
        self.assertEqual(payload['mode'],'dismiss_missing')
        self.assertEqual(payload['missing_dialog_runtime_id'],'missing-modal')
        r=missing_report(j,'dismiss_missing')
        self.queue.save_username_missing_result(self.qid,j['id'],j['item_id'],r)
        self.queue.finish_username_missing(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM contact_queue_openings').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM contact_queue_submissions').fetchone()[0],0)

    def test_recovery_rejects_submit_evidence_or_wrong_dialog(self):
        j=self.stopped_opening();reads=missing_reads(j)
        with self.assertRaises(ValueError):self.queue.claim_username_missing(self.qid,j['id'],missing_reads({**j,'item':{**j['item'],'value':'@other_user'}}))
        row=self.store.db.execute('SELECT report_json FROM contact_queue_openings').fetchone()
        r=json.loads(row[0]);r['create_attempted']=True
        with self.store.db:self.store.db.execute('UPDATE contact_queue_openings SET report_json=?',(json.dumps(r),))
        with self.assertRaises(ValueError):self.queue.claim_username_missing(self.qid,j['id'],reads)
        self.assertEqual(self.store.next_contact_number(),254)

    def test_late_result_after_stop_does_not_skip_current_or_next_username(self):
        j=self.opening();self.queue.stop(self.qid)
        self.queue.save_open_result(self.qid,j['id'],j['item_id'],missing_report(j))
        with self.assertRaises(ValueError):self.queue.finish_open(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(next(x for x in self.store.rows() if x['id']==j['item_id'])['status'],'reserved')

    def test_recovery_late_result_and_duplicate_completion_cannot_advance(self):
        j=self.stopped_opening();self.queue.claim_username_missing(self.qid,j['id'],missing_reads(j))
        self.queue.stop(self.qid)
        self.queue.save_username_missing_result(self.qid,j['id'],j['item_id'],missing_report(j,'dismiss_missing'))
        with self.assertRaises(ValueError):self.queue.finish_username_missing(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)
        with self.assertRaises(ValueError):self.queue.save_username_missing_result(self.qid,j['id'],j['item_id'],missing_report(j,'dismiss_missing'))

    def test_auto_callback_schedules_next_user_without_filling_missing_user(self):
        j=self.opening();a=self.app();scheduled=[];a.root.after=lambda delay,callback:scheduled.append((delay,callback))
        a.contact_results.put(('open',self.qid,j,missing_report(j),None))
        with patch.object(a,'start_contact_fill') as fill:
            a.poll_contact_worker();fill.assert_not_called()
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        self.assertTrue(any(delay==0 and callback==a.drive_contact_queue for delay,callback in scheduled))

    def test_restart_preserves_nonexistent_status_and_number(self):
        j=self.opening();self.queue.save_open_result(self.qid,j['id'],j['item_id'],missing_report(j))
        self.queue.finish_open(self.qid,j['id'],j['item_id'])
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.assertEqual(next(x for x in self.store.rows() if x['id']==j['item_id'])['status'],'username_not_found')
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertIsNone(self.queue.current(self.qid))

    def test_record_csv_exposes_nonexistent_status_and_reason(self):
        j=self.opening();self.queue.save_open_result(self.qid,j['id'],j['item_id'],missing_report(j))
        self.queue.finish_open(self.qid,j['id'],j['item_id'])
        target=Path(self.tmp.name)/'records.csv';self.store.export_csv(target)
        self.assertIn('用户名不存在',target.read_text(encoding='utf-8-sig'))

    def test_error_paused_resolution_is_automatically_checked_once(self):
        self.stopped_opening();a=self.app()
        with patch.object(a,'recover_username_missing') as recover:
            a.drive_contact_queue();a.drive_contact_queue()
            recover.assert_called_once_with(auto=True)

    def test_user_stop_is_not_automatically_resumed(self):
        self.stopped_opening();self.queue.stop(self.qid);a=self.app()
        with patch.object(a,'recover_username_missing') as recover:
            a.drive_contact_queue();recover.assert_not_called()

    def test_auto_recovery_callback_closes_and_advances_without_new_open(self):
        j=self.stopped_opening();a=self.app()
        from test_queue_opening import ImmediateThread
        a.contact_results.put(('recover_username_missing_auto',self.qid,j,missing_reads(j),None))
        with patch('app.threading.Thread',ImmediateThread),patch('app.run_window_script',return_value=missing_report(j,'dismiss_missing')) as native:
            a.poll_contact_worker()
            self.assertEqual(native.call_args.args[2]['mode'],'dismiss_missing')
            a.poll_contact_worker()
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        self.assertEqual(self.store.next_contact_number(),254)

    def test_official_english_missing_message_replayed_and_skipped(self):
        j=self.opening();reads=missing_reads(j)
        for r in reads:
            next(c for c in r['controls'] if c['type']=='Text')['name']=missing_username_text(j['item']['value'],'en')
        self.assertIsNotNone(missing_username_evidence(reads,j['window'],j['item']['value']))
        report=missing_report(j);report['not_found_text']=missing_username_text(j['item']['value'],'en')
        self.queue.save_open_result(self.qid,j['id'],j['item_id'],report)
        self.queue.finish_open(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        self.assertEqual(self.store.next_contact_number(),254)

    def test_english_missing_result_recovers_stopped_resolution(self):
        j=self.stopped_opening();reads=missing_reads(j)
        for r in reads:
            next(c for c in r['controls'] if c['type']=='Text')['name']=missing_username_text(j['item']['value'],'en')
        claimed,payload=self.queue.claim_username_missing(self.qid,j['id'],reads)
        report=missing_report(j,'dismiss_missing');report['not_found_text']=missing_username_text(j['item']['value'],'en')
        self.queue.save_username_missing_result(self.qid,j['id'],j['item_id'],report)
        self.queue.finish_username_missing(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        self.assertEqual(self.store.next_contact_number(),254)

    def test_english_wrong_username_vague_text_and_language_changes_rejected(self):
        j=self.queue.current(self.qid);reads=missing_reads(j)
        for text in ('Username @other_user not found.','Username not found.','Too many attempts. Please try again later.'):
            r=copy.deepcopy(reads)
            for report in r:next(c for c in report['controls'] if c['type']=='Text')['name']=text
            self.assertIsNone(missing_username_evidence(r,j['window'],j['item']['value']))
        next(c for c in reads[1]['controls'] if c['type']=='Text')['name']=missing_username_text(j['item']['value'],'en')
        self.assertIsNone(missing_username_evidence(reads,j['window'],j['item']['value']))
