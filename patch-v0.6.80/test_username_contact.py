import copy,json,queue,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contact_queue import ContactQueue
from store import Store
from username_contact import username_profile_verified,username_form_schema,opening_verified,fill_verified,submission_verified
from test_contact_queue import window
from test_queue_opening import ImmediateThread


def fixture(name):
    return json.loads((Path(__file__).parent/'test_fixtures'/('username-'+name+'.json')).read_text())


def opened(job):
    return {'ok':True,'scope':'username_contact','mode':'open','stage':'verified','state':'username_form_open',
        'window_handle':job['window']['hwnd'],'process_id':job['window']['pid'],'executable_path':job['window']['path'],
        'username':job['item']['value'],'main_runtime_id':'root-1','profile_runtime_id':'test-profile',
        'contact_runtime_id':'form-1','process_path_verified':True,'username_verified':True,'field_schema_verified':True,
        'default_instance_route_verified':True,'add_invoked':True,'fields_written':False,'create_attempted':False,'create_invoked':False,
        'final_invite_clicked':False,'contact_database_updated':False,'errors':[]}


def filled(job):
    r=opened(job);r.update(mode='fill',state='filled',number=str(job['preview_number']),fields_written=True,fields_verified=True)
    return r


def submitted(job):
    r=filled(job);r.update(mode='submit',stage='submitted',state='profile_opened',fields_written=False,
        create_attempted=True,create_invoked=True,create_runtime_id='done-1',profile_verified=True)
    return r


class UsernameContactTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite3'
        self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.store.import_text('phone','+5516991234567')
        self.store.import_text('username','@test_user\n@next_user\n@last_user')
        self.store.set_next_number(254)
        self.qid=self.queue.configure([{'account':'账号3','target':2,'window':window(1)}],new_batch=True,independent=True,
            auto_submit=True,auto_close_profile=True,contact_source='username')
        self.queue.start(self.qid)

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def fill(self):
        j=self.queue.current(self.qid);j=self.queue.claim_open(self.qid,j['id'])
        self.queue.save_open_result(self.qid,j['id'],j['item_id'],opened(j));self.queue.finish_open(self.qid,j['id'],j['item_id'])
        j=self.queue.claim_fill(self.qid,j['id']);self.queue.finish_fill(self.qid,j['id'],filled(j))
        return self.queue.current(self.qid)

    def submit(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id'])
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],submitted(j))
        self.queue.finish_submit(self.qid,j['id'],j['item_id'])
        return self.queue.current(self.qid)

    def test_recover_submitted_username_records_number_once(self):
        j=self.submit();self.queue.stop(self.qid,'核查已点击Done',review=True)
        r=fixture('after')
        next(c for c in r['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        bad=copy.deepcopy(r)
        next(c for c in bad['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='Original Name'
        with self.assertRaises(ValueError):self.queue.recover_submitted_profile(self.qid,j['id'],[bad,bad])
        self.assertEqual(self.store.next_contact_number(),254)
        self.queue.recover_submitted_profile(self.qid,j['id'],[r,copy.deepcopy(r)])
        self.assertEqual(self.store.next_contact_number(),255)
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        with self.assertRaises(ValueError):self.queue.recover_submitted_profile(self.qid,j['id'],[r,r])
        self.assertEqual(self.store.next_contact_number(),255)

    def test_manual_record_syncs_queue_and_recovery_does_not_number_twice(self):
        j=self.submit();self.queue.stop(self.qid,'手动已添加',review=True)
        self.assertEqual(self.store.record_add_result(j['item_id'],'added'),254)
        self.assertEqual(self.queue.snapshot(self.qid)['jobs'][0]['state'],'manual_added_pending_profile')
        r=fixture('after')
        next(c for c in r['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        bad=copy.deepcopy(r)
        next(c for c in bad['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='999'
        with self.assertRaises(ValueError):self.queue.recover_submitted_profile(self.qid,j['id'],[bad,bad])
        self.assertEqual(self.store.next_contact_number(),255)
        self.queue.recover_submitted_profile(self.qid,j['id'],[r,copy.deepcopy(r)])
        self.assertEqual(self.store.next_contact_number(),255)
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        saved=self.store.db.execute('SELECT * FROM items WHERE id=?',(j['item_id'],)).fetchone()
        self.assertEqual(saved['contact_number'],254)
        self.assertEqual(saved['status'],'added')

    def test_legacy_manual_record_stuck_submitting_can_reconcile(self):
        j=self.submit();self.queue.stop(self.qid,'旧版手动登记',review=True)
        self.store.record_add_result(j['item_id'],'added')
        with self.store.db:self.store.db.execute("UPDATE contact_queue_jobs SET state='submitting' WHERE id=?",(j['id'],))
        r=fixture('after')
        next(c for c in r['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        self.queue.recover_submitted_profile(self.qid,j['id'],[r,copy.deepcopy(r)])
        self.assertEqual(self.store.next_contact_number(),255)
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')

    def test_manual_record_final_target_finishes_without_extra_reservation(self):
        j=self.submit();self.queue.stop(self.qid,'手动已添加',review=True)
        with self.store.db:self.store.db.execute('UPDATE batches SET target=1 WHERE id=?',(j['batch_id'],))
        self.store.record_add_result(j['item_id'],'added')
        r=fixture('after')
        next(c for c in r['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        self.queue.recover_submitted_profile(self.qid,j['id'],[r,copy.deepcopy(r)])
        self.assertEqual(self.store.next_contact_number(),255)
        self.assertIsNone(self.queue.current(self.qid))
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'done')

    def test_manual_record_while_running_rolls_back_number_and_item(self):
        j=self.submit()
        with self.assertRaisesRegex(ValueError,'停止队列'):self.store.record_add_result(j['item_id'],'added')
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(self.queue.current(self.qid)['item']['status'],'reserved')

    def paused_unsaved_submission(self):
        j=self.submit()
        row=self.store.db.execute('SELECT report_json FROM contact_queue_submissions WHERE job_id=? AND item_id=?',(j['id'],j['item_id'])).fetchone()
        r=json.loads(row[0]);r.update(ok=False,stage='wait_profile',state='review',profile_verified=False,errors=['Not saved'])
        with self.store.db:self.store.db.execute('UPDATE contact_queue_submissions SET report_json=?,error=? WHERE job_id=? AND item_id=?',(json.dumps(r),'Not saved',j['id'],j['item_id']))
        self.queue.stop(self.qid,'Not saved',review=True)
        return j

    def test_unsaved_retry_preserves_item_number_audit_and_blocks_second_retry(self):
        j=self.paused_unsaved_submission();r=fixture('before')
        resumed=self.queue.retry_unsaved_username(self.qid,[r,copy.deepcopy(r)])
        self.assertEqual(resumed['item_id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(resumed['retry_unsaved_profile_runtime_id'],r['scope_runtime_id'])
        self.assertEqual(len(self.queue.snapshot(self.qid)['username_retry_history']),1)
        self.assertIsNone(resumed['fill_report'])
        self.assertIsNone(resumed['opening_report'])
        self.paused_unsaved_submission()
        with self.assertRaises(ValueError):self.queue.retry_unsaved_username(self.qid,[r,r])
        self.assertEqual(self.store.next_contact_number(),254)

    def test_saved_contact_after_retry_open_recovers_from_archived_submission(self):
        j=self.paused_unsaved_submission();before=fixture('before')
        self.queue.retry_unsaved_username(self.qid,[before,copy.deepcopy(before)])
        reopened=self.queue.claim_open(self.qid,j['id']);r=opened(reopened)
        r.update(ok=False,stage='resolve_requested',state='review',add_invoked=False,username_verified=False,
                 errors=['User already a contact'])
        self.queue.save_open_result(self.qid,j['id'],j['item_id'],r,'already contact')
        self.queue.stop(self.qid,'already contact',review=True)
        saved=fixture('after')
        next(c for c in saved['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        recovered=self.queue.recover_submitted_profile(self.qid,j['id'],[saved,copy.deepcopy(saved)])
        self.assertEqual(recovered['preview_number'],254)
        self.assertEqual(self.store.next_contact_number(),255)
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        self.assertEqual(self.queue.current(self.qid)['retry_unsaved_profile_runtime_id'],'')

    def test_unsaved_retry_rejects_saved_wrong_user_and_changing_profile(self):
        j=self.paused_unsaved_submission();before=fixture('before')
        saved=fixture('after')
        for reports in ([saved,saved],[before,dict(before,scope_runtime_id='different')]):
            with self.assertRaises(ValueError):self.queue.retry_unsaved_username(self.qid,reports)
        bad=copy.deepcopy(before)
        next(c for c in bad['controls'] if str(c.get('name','')).lower()=='@test_user')['name']='@other'
        with self.assertRaises(ValueError):self.queue.retry_unsaved_username(self.qid,[bad,bad])
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'review')
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(len(self.queue.snapshot(self.qid)['username_retry_history']),0)

    def test_resume_unclicked_form_keeps_original_fill_and_number(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(ok=False,stage='initial',state='review',create_attempted=False,create_invoked=False,profile_verified=False,errors=['No clickable point'])
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r,'No clickable point')
        self.queue.stop(self.qid,'No clickable point',review=True)
        resumed=self.queue.resume_unclicked_username(self.qid)
        self.assertEqual(resumed['state'],'filled');self.assertEqual(resumed['fill_report'],j['fill_report'])
        self.assertEqual(self.store.next_contact_number(),254)
        claimed=self.queue.claim_submit(self.qid,j['id'])
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r,'again')
        self.queue.stop(self.qid,'again',review=True)
        with self.assertRaises(ValueError):self.queue.resume_unclicked_username(self.qid)

    def test_known_two_preclick_faults_can_resume_after_target_fix_once(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(ok=False,stage='initial',state='review',create_attempted=False,create_invoked=False,profile_verified=False,
                 errors=['GetClickablePoint failed'])
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r,'No point')
        self.queue.stop(self.qid,'No point',review=True)
        self.queue.resume_unclicked_username(self.qid)
        self.queue.claim_submit(self.qid,j['id'])
        r['errors']=['Done click target changed; not clicked.']
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r,'target')
        self.queue.stop(self.qid,'target',review=True)
        self.queue.resume_unclicked_username(self.qid)
        audit=self.store.db.execute('SELECT previous_json FROM contact_queue_unclicked_retries').fetchone()
        self.assertEqual(len(json.loads(audit[0])['attempts']),2)
        self.assertEqual(self.store.next_contact_number(),254)
        self.queue.claim_submit(self.qid,j['id'])
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r,'target again')
        self.queue.stop(self.qid,'target again',review=True)
        with self.assertRaises(ValueError):self.queue.resume_unclicked_username(self.qid)

    def test_resume_unclicked_rejects_already_invoked_done(self):
        self.paused_unsaved_submission()
        with self.assertRaises(ValueError):self.queue.resume_unclicked_username(self.qid)
        self.assertEqual(self.store.next_contact_number(),254)

    def test_dispatched_done_waits_for_independent_saved_profile_without_resubmit(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(state='awaiting_profile',profile_verified=False,
                 submit_method='verified_native_window_mouse_click',click_target_verified=True)
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r)
        self.queue.finish_submit(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.queue.current(self.qid)['state'],'submitted')
        self.assertEqual(self.store.next_contact_number(),254)
        before=fixture('before');before['scope_runtime_id']='fresh-profile'
        self.assertIsNone(self.queue.accept(self.qid,j['id'],[before,copy.deepcopy(before)]))
        after=fixture('after');after['scope_runtime_id']='fresh-profile'
        next(c for c in after['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        self.assertIsNone(self.queue.accept(self.qid,j['id'],[after]))
        self.queue.accept(self.qid,j['id'],[after,copy.deepcopy(after)])
        self.assertEqual(self.store.next_contact_number(),255)
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')

    def test_ui_dispatched_pending_waits_then_closes_success_without_second_done(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(state='awaiting_profile',profile_verified=False,submit_method='verified_native_window_mouse_click',click_target_verified=True)
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r)
        self.queue.finish_submit(self.qid,j['id'],j['item_id'])
        j=self.queue.current(self.qid);a=self.app();scheduled=[]
        a.root.after=lambda delay,fn:scheduled.append((delay,fn))
        before=fixture('before');a.contact_results.put(('observe',self.qid,j,[before,copy.deepcopy(before)],None))
        with patch.object(a,'start_contact_submit') as submit_again:
            a.poll_contact_worker();submit_again.assert_not_called()
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'running')
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertTrue(any(delay==500 for delay,fn in scheduled))
        after=fixture('after')
        next(c for c in after['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        a.contact_results.put(('observe',self.qid,j,[after,copy.deepcopy(after)],None))
        with patch.object(a,'start_saved_username_close') as close,patch.object(a,'start_contact_submit') as submit_again:
            a.poll_contact_worker();close.assert_called_once();submit_again.assert_not_called()
        self.assertEqual(self.store.next_contact_number(),255)

    def test_dispatch_rejects_missing_click_target_proof(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(state='awaiting_profile',profile_verified=False,submit_method='verified_native_window_mouse_click',click_target_verified=False)
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r)
        with self.assertRaises(ValueError):self.queue.finish_submit(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)

    def test_keyboard_submit_requires_verified_last_name_focus(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(state='awaiting_profile',profile_verified=False,submit_method='verified_focused_last_name_enter',
                 focus_target_verified=True,focus_reads=2,submit_focus_runtime_id='last-field',
                 actual_focus_runtime_id='last-field',actual_focus_name='Last name',actual_focus_class='class Ui::InputField::Inner')
        self.assertTrue(submission_verified(r,j))
        for key,value in (('actual_focus_name','Write a message...'),('actual_focus_runtime_id','chat-input'),
                          ('focus_target_verified',False),('focus_reads',1),('actual_focus_class','class MainWindow')):
            bad=dict(r);bad[key]=value
            self.assertFalse(submission_verified(bad,j),key)
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r)
        self.queue.finish_submit(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(self.queue.current(self.qid)['state'],'submitted')

    def test_done_space_requires_button_focus_and_complete_input_delivery(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(state='awaiting_profile',profile_verified=False,submit_method='verified_focused_done_space',
                 focus_target_verified=True,focus_reads=2,submit_focus_runtime_id='done-button',
                 actual_focus_runtime_id='done-button',actual_focus_name='Done',actual_focus_class='class Ui::RoundButton',
                 create_runtime_id='done-button',submit_key_down_count=1,submit_key_up_count=1)
        self.assertTrue(submission_verified(r,j))
        for key,value in (('actual_focus_name','Cancel'),('actual_focus_class','class Ui::InputField::Inner'),
                          ('actual_focus_runtime_id','chat-input'),('submit_key_down_count',0),('submit_key_up_count',0)):
            bad=dict(r);bad[key]=value
            self.assertFalse(submission_verified(bad,j),key)
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r)
        self.queue.finish_submit(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(self.queue.current(self.qid)['state'],'submitted')
        saved=fixture('after');saved['scope_runtime_id']='new-saved-profile'
        next(c for c in saved['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        self.queue.accept(self.qid,j['id'],[saved,copy.deepcopy(saved)])
        self.assertEqual(self.store.next_contact_number(),255)

    def test_retry_accepts_v76_dispatched_pending_when_two_reads_prove_unsaved(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(state='awaiting_profile',profile_verified=False,submit_method='verified_focused_last_name_enter',
                 focus_target_verified=True,focus_reads=2,submit_focus_runtime_id='last',actual_focus_runtime_id='last',
                 actual_focus_name='Last name',actual_focus_class='class Ui::InputField::Inner')
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r)
        self.queue.finish_submit(self.qid,j['id'],j['item_id'])
        self.queue.stop(self.qid,'No saved contact',review=True)
        before=fixture('before')
        resumed=self.queue.retry_unsaved_username(self.qid,[before,copy.deepcopy(before)])
        self.assertEqual(resumed['item_id'],j['item_id'])
        self.assertEqual(resumed['state'],'waiting_form')
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(len(self.queue.snapshot(self.qid)['username_retry_history']),1)

    def test_retry_pending_rejects_missing_delivery_proof_without_releasing_item(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(state='awaiting_profile',profile_verified=False,submit_method='verified_focused_done_space',
                 focus_target_verified=True,focus_reads=2,submit_focus_runtime_id='done',actual_focus_runtime_id='done',
                 actual_focus_name='Done',actual_focus_class='class Ui::RoundButton',create_runtime_id='done',
                 submit_key_down_count=1,submit_key_up_count=0)
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r)
        self.queue.stop(self.qid,'Delivery failed',review=True)
        before=fixture('before')
        with self.assertRaises(ValueError):self.queue.retry_unsaved_username(self.qid,[before,before])
        self.assertEqual(self.queue.snapshot(self.qid)['jobs'][0]['item_id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)

    def test_unfocusable_done_recovers_original_form_then_verified_enter_counts_once(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(ok=False,stage='initial',state='review',create_attempted=False,create_invoked=False,profile_verified=False,
                 errors=['SetFocus: target cannot receive focus'])
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r,'SetFocus failed')
        self.queue.stop(self.qid,'SetFocus failed',review=True)
        resumed=self.queue.resume_unclicked_username(self.qid)
        self.assertEqual(resumed['fill_report'],j['fill_report'])
        self.assertEqual(self.store.next_contact_number(),254)
        j=self.queue.claim_submit(self.qid,j['id']);r=submitted(j)
        r.update(state='awaiting_profile',profile_verified=False,submit_method='verified_focused_last_name_enter_sendinput',
                 focus_target_verified=True,focus_reads=2,submit_focus_runtime_id='last-field',actual_focus_runtime_id='last-field',
                 actual_focus_name='Last name',actual_focus_class='class Ui::InputField::Inner',
                 submit_key_down_count=1,submit_key_up_count=1,submit_scan_code=28)
        self.assertTrue(submission_verified(r,j))
        for key,value in (('actual_focus_name','Write a message...'),('actual_focus_runtime_id','chat'),
                          ('submit_key_down_count',0),('submit_key_up_count',0),('submit_scan_code',0)):
            bad=dict(r);bad[key]=value
            self.assertFalse(submission_verified(bad,j),key)
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],r)
        self.queue.finish_submit(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)
        saved=fixture('after');saved['scope_runtime_id']='fresh-saved-profile'
        next(c for c in saved['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        self.queue.accept(self.qid,j['id'],[saved,copy.deepcopy(saved)])
        self.assertEqual(self.store.next_contact_number(),255)
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')

    def test_replay_real_reports_identifies_add_form_and_saved_contact(self):
        self.assertTrue(username_profile_verified(fixture('before'),window(1),'@test_user'))
        self.assertTrue(username_form_schema(fixture('form'),window(1)))
        self.assertTrue(username_profile_verified(fixture('after'),window(1),'@test_user','123'))
        self.assertFalse(username_profile_verified(fixture('before'),window(1),'@test_user','123'))
        self.assertFalse(username_profile_verified(fixture('after'),window(1),'@test_user'))

    def test_wrong_username_number_window_or_incomplete_profile_rejected(self):
        r=fixture('after')
        self.assertFalse(username_profile_verified(r,window(1),'@other_user',123))
        self.assertFalse(username_profile_verified(r,window(1),'@test_user',124))
        self.assertFalse(username_profile_verified(r,window(2),'@test_user',123))
        for key,value in [('truncated',True),('errors',['failed']),('read_only',False),('scope','dialog')]:
            bad=copy.deepcopy(r);bad[key]=value
            self.assertFalse(username_profile_verified(bad,window(1),'@test_user',123))

    def test_add_marker_and_duplicate_title_block_false_success(self):
        r=fixture('after');r['controls'].append(next(c for c in fixture('before')['controls'] if c.get('class_name')=='class Ui::MarqueeLabel'))
        self.assertFalse(username_profile_verified(r,window(1),'@test_user',123))
        r=fixture('after');r['controls'].append({'type':'Button','name':'ADICIONAR CONTATO','class_name':'class Ui::SettingsButton','visible':True,'enabled':True})
        self.assertFalse(username_profile_verified(r,window(1),'@test_user',123))

    def test_form_uses_inner_fields_rejects_extra_or_phone_fields(self):
        r=fixture('form');self.assertTrue(username_form_schema(r,window(1)))
        field=next(c for c in r['controls'] if c.get('class_name')=='class Ui::InputField::Inner')
        for kind in ('duplicate','missing_pattern','phone'):
            bad=copy.deepcopy(r)
            if kind=='duplicate':bad['controls'].append(copy.deepcopy(field))
            if kind=='missing_pattern':next(c for c in bad['controls'] if c.get('class_name')=='class Ui::InputField::Inner')['value_pattern']=False
            if kind=='phone':bad['controls'].append({'type':'Edit','class_name':'class Ui::PhoneInput','visible':True})
            self.assertFalse(username_form_schema(bad,window(1)))

    def test_reserved_username_not_phone_and_number_allocated_only_after_success(self):
        j=self.submit();self.assertEqual(j['item']['source'],'username')
        self.assertEqual(self.store.batch(j['batch_id'])['mode'],'username')
        self.assertEqual(self.store.next_contact_number(),254)
        r=fixture('after');next(c for c in r['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        self.assertEqual(self.queue.accept(self.qid,j['id'],[r,copy.deepcopy(r)])['outcome'],'added')
        self.assertEqual(self.store.next_contact_number(),255)
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')
        self.assertEqual(next(r for r in self.store.rows() if r['source']=='phone')['status'],'ready')
        with self.assertRaises(ValueError):self.queue.accept(self.qid,j['id'],[r,copy.deepcopy(r)])
        self.assertEqual(self.store.next_contact_number(),255)

    def test_fill_without_original_opening_proof_is_blocked(self):
        j=self.queue.current(self.qid)
        with self.assertRaises(ValueError):self.queue.claim_fill(self.qid,j['id'])
        self.assertEqual(self.store.next_contact_number(),254)

    def test_wrong_opening_username_never_allows_fill(self):
        j=self.queue.current(self.qid);j=self.queue.claim_open(self.qid,j['id']);r=opened(j)
        r['username']='@other_user'
        self.queue.save_open_result(self.qid,j['id'],j['item_id'],r)
        with self.assertRaises(ValueError):self.queue.finish_open(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)

    def test_wrong_form_or_identity_rejected_before_submit(self):
        j=self.fill();r=filled(j)
        for key,value in [('contact_runtime_id','other'),('username','@other_user'),('number','255'),('fields_verified',False)]:
            bad=copy.deepcopy(r);bad[key]=value
            self.assertFalse(fill_verified(bad,j))
        r=submitted(j);r['create_invoked']=False;self.assertFalse(submission_verified(r,j))

    def test_stop_and_late_result_never_advance_or_replay(self):
        j=self.fill();j=self.queue.claim_submit(self.qid,j['id']);self.queue.stop(self.qid)
        self.queue.save_submit_result(self.qid,j['id'],j['item_id'],submitted(j))
        with self.assertRaises(ValueError):self.queue.finish_submit(self.qid,j['id'],j['item_id'])
        self.assertEqual(self.store.next_contact_number(),254)
        self.assertEqual(self.store.rows()[1]['status'],'reserved')

    def test_restart_never_resubmits_pending_user(self):
        j=self.fill();self.queue.claim_submit(self.qid,j['id'])
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.assertEqual(self.queue.snapshot(self.qid)['contact_source'],'username')
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'review')
        self.assertIsNone(self.queue.current(self.qid))
        self.assertEqual(self.store.next_contact_number(),254)

    def test_username_start_sequence_keeps_earlier_names_ready(self):
        self.queue.stop(self.qid)
        # The existing reserved username cannot be recycled into a new batch.
        with self.assertRaises(ValueError):
            self.queue.configure([{'account':'账号3','target':1,'window':window(1)}],new_batch=True,independent=True,
                auto_submit=True,contact_source='username',start_username_seq=2)
        qid=self.queue.configure([{'account':'账号4','target':1,'window':window(2)}],new_batch=True,independent=True,
            auto_submit=True,contact_source='username',start_username_seq=3)
        self.assertEqual(self.queue.start(qid)['item']['value'],'@last_user')

    def app(self):
        from app import App
        a=App.__new__(App);a.store=self.store;a.adding_queue=self.queue;a.probing=False
        a.contact_results=queue.Queue();a.root=SimpleNamespace(after=lambda *args:None)
        a.probe_button=SimpleNamespace(configure=lambda **kwargs:None);a.status=SimpleNamespace(set=lambda value:None)
        a.refresh=lambda **kwargs:None;a.refresh_adding_queue=lambda:None
        return a

    def test_native_worker_routes_open_fill_submit_to_username_script(self):
        a=self.app();j=self.queue.current(self.qid)
        def runner(w,script,payload):
            self.assertEqual(script,'username_contact.ps1')
            current=self.queue.current(self.qid)
            if payload['mode']=='open':return opened(current)
            if payload['mode']=='fill':return filled(current)
            return submitted(current)
        with patch('app.threading.Thread',ImmediateThread),patch('app.run_window_script',side_effect=runner):
            a.start_contact_open(self.qid,j['id'])
            a.poll_contact_worker() # open => fill
            a.poll_contact_worker() # fill => submit
            a.poll_contact_worker() # submit => observation scheduled
        self.assertEqual(self.queue.current(self.qid)['state'],'submitted')
        self.assertEqual(self.store.next_contact_number(),254)

    def test_switch_from_phone_preserves_saved_contacts_for_later_invitation(self):
        self.queue.stop(self.qid)
        batch=self.store.create_batch('账号4',target=1)
        item,_=self.store.reserve_next(batch,phone_only=True)
        self.store.record_add_result(item['id'],'added')
        qid=self.queue.configure([{'account':'账号4','target':1,'window':window(2)}],
            new_batch=True,independent=True,auto_submit=True,contact_source='username')
        self.assertEqual(self.store.batch(batch)['status'],'waiting')
        saved=next(r for r in self.store.rows() if r['id']==item['id'])
        self.assertEqual(saved['status'],'added')
        self.assertEqual(saved['contact_number'],254)
        self.assertEqual(self.store.batch(self.queue.snapshot(qid)['jobs'][0]['batch_id'])['mode'],'username')

    def test_two_accounts_keep_global_numbering_and_separate_username_reservations(self):
        self.queue.stop(self.qid)
        self.qid=self.queue.configure([{'account':a,'target':1,'window':window(i)} for i,a in enumerate(('账号4','账号5'),1)],
            new_batch=True,independent=True,auto_submit=True,auto_close_profile=True,contact_source='username')
        self.queue.start(self.qid)
        for account,number in [('账号4',254),('账号5',255)]:
            j=self.submit();self.assertEqual(j['account'],account)
            r=fixture('after');r['window_handle']=j['window']['hwnd'];r['process_id']=j['window']['pid']
            for c in r['controls']:
                if c.get('name')=='@test_user':c['name']=j['item']['value']
                if c.get('class_name')=='class Ui::MarqueeLabel':c['name']=str(number)
            self.assertEqual(self.queue.accept(self.qid,j['id'],[r,copy.deepcopy(r)])['outcome'],'added')
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'done')
        self.assertEqual(self.store.next_contact_number(),256)

    def test_real_background_open_worker_has_no_sqlite_access(self):
        import threading
        real_thread=threading.Thread
        class JoinedThread:
            def __init__(self,**kwargs):self.worker=real_thread(**kwargs)
            def start(self):
                self.worker.start();self.worker.join(timeout=5)
                if self.worker.is_alive():raise AssertionError('worker did not complete')
        a=self.app();j=self.queue.current(self.qid)
        with patch('app.threading.Thread',JoinedThread),patch('app.run_window_script',return_value=opened(j)):
            a.start_contact_open(self.qid,j['id'])
        mode,qid,claimed,report,error=a.contact_results.get_nowait()
        self.assertIsNone(error)
        self.assertEqual((mode,qid),('open',self.qid))
        self.assertTrue(opening_verified(report,claimed))

    def test_ui_success_closes_saved_username_before_next_open(self):
        j=self.submit();r=fixture('after');next(c for c in r['controls'] if c.get('class_name')=='class Ui::MarqueeLabel')['name']='254'
        a=self.app();scheduled=[];a.root.after=lambda delay,callback:scheduled.append((delay,callback))
        a.contact_results.put(('observe',self.qid,j,[r,copy.deepcopy(r)],None))
        with patch.object(a,'start_contact_profile_close') as phone_close, patch.object(a,'start_saved_username_close') as close:
            a.poll_contact_worker();phone_close.assert_not_called()
            close.assert_called_once_with(self.qid,j,[r,copy.deepcopy(r)])
        self.assertFalse(any(delay==0 and callback==a.drive_contact_queue for delay,callback in scheduled))
        self.assertEqual(self.queue.current(self.qid)['item']['value'],'@next_user')

    def test_official_english_profile_button_and_contact_markers(self):
        before=fixture('before');after=fixture('after')
        for c in before['controls']:
            if c.get('name')=='ADICIONAR CONTATO':c['name']='Add to contacts'
        for c in after['controls']:
            if c.get('name')=='Editar contato':c['name']='Edit contact'
            if c.get('name')=='Apagar contato':c['name']='Delete contact'
        self.assertTrue(username_profile_verified(before,window(1),'@test_user'))
        self.assertTrue(username_profile_verified(after,window(1),'@test_user','123'))
        for c in before['controls']:
            if c.get('name')=='Add to contacts':c['name']='ADD TO CONTACTS'
        self.assertTrue(username_profile_verified(before,window(1),'@test_user'))

    def test_official_english_form_labels_keep_same_structural_guards(self):
        form=fixture('form')
        labels={'Novo Contato':'New Contact','Nome':'First name','Sobrenome':'Last name','Nota':'Note','Pronto':'Done','Cancelar':'Cancel'}
        for c in form['controls']:
            if c.get('name') in labels:c['name']=labels[c['name']]
        self.assertTrue(username_form_schema(form,window(1)))
        next(c for c in form['controls'] if c.get('class_name')=='class Ui::InputField::Inner')['value_pattern']=False
        self.assertFalse(username_form_schema(form,window(1)))


class SavedUsernameCloseTests(unittest.TestCase):
    def test_close_result_requires_correct_profile_number_window_and_no_add(self):
        from username_contact import saved_profile_close_verified
        w=window(1)
        j={'window':w,'item':{'value':'@test_user'},'preview_number':263,
           'opening_report':{'main_runtime_id':'root'},'close_profile_runtime_id':'profile'}
        r={'ok':True,'scope':'username_contact','mode':'close_saved','state':'saved_profile_closed',
           'window_handle':w['hwnd'],'process_id':w['pid'],'executable_path':w['path'],
           'username':'@test_user','number':'263','main_runtime_id':'root','profile_runtime_id':'profile',
           'errors':[]}
        for k in ('process_path_verified','profile_verified','close_attempted','close_invoked','profile_absent'):r[k]=True
        for k in ('fields_written','add_invoked','create_attempted','create_invoked'):r[k]=False
        self.assertTrue(saved_profile_close_verified(r,j))
        for key,value in [('number','264'),('username','@other'),('profile_runtime_id','other'),
                          ('window_handle',999),('profile_absent',False),('create_invoked',True)]:
            changed=dict(r);changed[key]=value
            self.assertFalse(saved_profile_close_verified(changed,j),key)
