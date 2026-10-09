import copy
import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from store import Store
from contact_queue import ContactQueue
from contact_submit import submission_payload, submission_verified
from test_contact_queue import window, profile, limit
from test_queue_opening import ImmediateThread
from controls_probe import WindowActionError


def filled(job):
    w=job['window']
    return {'ok':True,'mode':'fill_only','number':str(job['preview_number']),'phone_matches':True,
        'empty_guard':True,'contact_created':False,'final_invite_clicked':False,
        'window_handle':w['hwnd'],'process_id':w['pid'],'main_runtime_id':f'root:{w["hwnd"]}',
        'contact_runtime_id':f'contact:{w["hwnd"]}'}


def submitted(job,*,dialog=False):
    w=job['window'];f=job['fill_report']
    return {'ok':True,'scope':'contact_submit','stage':'submitted','state':'result_dialog' if dialog else 'profile_opened',
        'window_handle':w['hwnd'],'process_id':w['pid'],'main_runtime_id':f['main_runtime_id'],
        'contact_runtime_id':f['contact_runtime_id'],'number':str(job['preview_number']),
        'fields_verified':True,'process_path_verified':True,'create_attempted':True,'create_invoked':True,
        'create_runtime_id':'create-button','fields_written':False,'contact_created_verified':False,
        'contact_database_updated':False,'final_invite_clicked':False,'errors':[],
        'title_verified':not dialog,'profile_open_attempted':not dialog,'profile_open_invoked':not dialog,
        'profile_opened':not dialog,'profile_runtime_id':'profile-1' if not dialog else None,
        'result_runtime_id':'modal-1' if dialog else None}


class ContactSubmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite3'
        self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.store.import_text('phone','+5516991234501\n+5516991234502\n+5516991234503')
        self.store.set_next_number(12)
        self.qid=self.queue.configure([{'account':a,'target':1,'window':window(i)} for i,a in enumerate(('A','B'),1)],auto_submit=True)
        self.queue.start(self.qid)

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def fill(self):
        job=self.queue.current(self.qid);job=self.queue.claim_fill(self.qid,job['id'])
        self.queue.finish_fill(self.qid,job['id'],filled(job));return self.queue.current(self.qid)

    def finish(self,job,r):
        self.queue.save_submit_result(self.qid,job['id'],job['item_id'],r)
        self.queue.finish_submit(self.qid,job['id'],job['item_id'])

    def app(self):
        from app import App
        a=App.__new__(App);a.store=self.store;a.adding_queue=self.queue;a.probing=False
        a.contact_results=queue.Queue();a.root=SimpleNamespace(after=lambda *args:None)
        a.probe_button=SimpleNamespace(configure=lambda **kwargs:None);a.status=SimpleNamespace(set=lambda v:None)
        a.refresh=lambda **kwargs:None;a.refresh_adding_queue=lambda:None;return a

    def test_submit_completion_schedules_verification_without_periodic_wait(self):
        job=self.fill();job=self.queue.claim_submit(self.qid,job['id'])
        a=self.app();scheduled=[];a.root.after=lambda delay,callback:scheduled.append((delay,callback))
        a.contact_results.put(('submit',self.qid,job,submitted(job),None))
        a.poll_contact_worker()
        self.assertEqual(self.queue.current(self.qid)['state'],'submitted')
        self.assertTrue(any(delay==0 and callback==a.drive_contact_queue for delay,callback in scheduled))
        self.queue.stop(self.qid)
        with patch('app.inspect_controls') as inspect:
            for delay,callback in scheduled:
                if delay==0:callback()
            inspect.assert_not_called()
        self.assertEqual(self.store.next_contact_number(),12)

    def test_success_reuses_verified_profile_pair_for_next_contact_close(self):
        job=self.fill();job=self.queue.claim_submit(self.qid,job['id']);self.finish(job,submitted(job))
        with self.store.db:
            self.store.db.execute('UPDATE batches SET target=2 WHERE id=?',(job['batch_id'],))
            self.store.db.execute('UPDATE contact_queues SET auto_close_profile=1 WHERE id=?',(self.qid,))
        a=self.app();r=profile(job);reads=[r,copy.deepcopy(r)]
        a.contact_results.put(('observe',self.qid,self.queue.current(self.qid),reads,None))
        with patch.object(a,'start_contact_profile_close') as close:
            a.poll_contact_worker()
            close.assert_called_once_with(self.qid,job['id'],reads)
        nxt=self.queue.current(self.qid)
        self.assertNotEqual(nxt['item_id'],job['item_id'])
        self.assertEqual(nxt['account'],'A')
        self.assertEqual(self.store.next_contact_number(),13)

    def test_A12_B13_create_each_once_then_single_bound_profile_allocates_success(self):
        for account,number in (('A',12),('B',13)):
            job=self.fill();job=self.queue.claim_submit(self.qid,job['id'])
            self.assertEqual((job['account'],job['preview_number'],job['state']),(account,number,'submitting'))
            self.assertEqual(self.store.next_contact_number(),number)
            self.finish(job,submitted(job));self.assertEqual(self.store.next_contact_number(),number)
            r=profile(job)
            self.assertEqual(self.queue.accept(self.qid,job['id'],[r])['outcome'],'added')
        s=self.queue.snapshot(self.qid);self.assertEqual(s['state'],'done');self.assertEqual(s['next_number'],14)
        self.assertTrue(s['contact_submit_automated']);self.assertFalse(s['final_invite_clicked'])
        path=Path(self.tmp.name)/'report.json';self.queue.export(path);saved=json.loads(path.read_text())
        self.assertEqual(len(saved['form_submissions']),2)
        self.assertEqual([a['number'] for a in saved['form_submissions']],[12,13])

    def test_form_identity_and_all_action_proofs_required(self):
        job=self.fill();r=submitted(job);self.assertTrue(submission_verified(r,job))
        changes={'window_handle':102,'process_id':999,'main_runtime_id':'changed','contact_runtime_id':'changed',
            'number':'13','fields_verified':False,'process_path_verified':False,'create_attempted':False,
            'create_invoked':False,'create_runtime_id':'','fields_written':True,'contact_created_verified':True,
            'contact_database_updated':True,'final_invite_clicked':True,'errors':['failed'],
            'title_verified':False,'profile_open_attempted':False,'profile_open_invoked':False,'profile_opened':False,'profile_runtime_id':''}
        for k,v in changes.items():
            altered=copy.deepcopy(r);altered[k]=v;self.assertFalse(submission_verified(altered,job),(k,v))

    def already_open(self,job):
        r=submitted(job)
        r.update(profile_open_attempted=False,profile_open_invoked=False,
                 profile_already_open=True,result_page_verified=True)
        return r

    def test_profile_opened_by_telegram_enters_original_success_verification(self):
        job=self.fill();job=self.queue.claim_submit(self.qid,job['id'])
        self.finish(job,self.already_open(job))
        self.assertEqual(self.store.next_contact_number(),12)
        self.assertEqual(self.queue.current(self.qid)['state'],'submitted')
        self.assertEqual(self.queue.accept(self.qid,job['id'],[profile(job)])['outcome'],'added')
        self.assertEqual(self.store.next_contact_number(),13)
        self.assertEqual(self.queue.current(self.qid)['account'],'B')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM contact_queue_submissions').fetchone()[0],1)

    def test_already_open_profile_needs_explicit_proof_without_claiming_an_info_click(self):
        job=self.fill();r=self.already_open(job)
        self.assertTrue(submission_verified(r,job))
        for key,value in (('result_page_verified',False),('profile_already_open',False),
                          ('profile_already_open',1),('profile_open_attempted',True),
                          ('profile_open_invoked',True),('profile_runtime_id',''),
                          ('title_verified',False),('window_handle',102),('number','13')):
            altered=copy.deepcopy(r);altered[key]=value
            self.assertFalse(submission_verified(altered,job),(key,value))
        del r['result_page_verified']
        self.assertFalse(submission_verified(r,job))

    def test_already_open_wrong_contact_or_changed_profile_never_advances(self):
        job=self.fill();job=self.queue.claim_submit(self.qid,job['id'])
        self.finish(job,self.already_open(job))
        r=profile(job);r['scope_runtime_id']='different'
        with self.assertRaisesRegex(ValueError,'身份'):
            self.queue.accept(self.qid,job['id'],[r])
        for label in ('+5516999999999','wrong number'):
            r=profile(job)
            r['controls'][0 if label.startswith('+') else 1]['name']=label
            self.assertIsNone(self.queue.accept(self.qid,job['id'],[r]))
            self.assertEqual(self.store.next_contact_number(),12)
        with self.assertRaises(ValueError):self.queue.claim_submit(self.qid,job['id'])

    def test_missing_fill_identity_or_global_number_change_never_claims_create(self):
        job=self.fill();old=self.queue.current(self.qid)['fill_report']
        for key in ('window_handle','process_id','main_runtime_id','contact_runtime_id'):
            changed=copy.deepcopy(old);changed.pop(key)
            with self.store.db:self.store.db.execute('UPDATE contact_queue_jobs SET fill_json=? WHERE id=?',(json.dumps(changed),job['id']))
            with self.assertRaises(ValueError):self.queue.claim_submit(self.qid,job['id'])
        with self.store.db:self.store.db.execute('UPDATE contact_queue_jobs SET fill_json=? WHERE id=?',(json.dumps(old),job['id']))
        self.store.set_next_number(20)
        with self.assertRaisesRegex(ValueError,'全局编号'):self.queue.claim_submit(self.qid,job['id'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM contact_queue_submissions').fetchone()[0],0)

    def test_replay_and_stop_after_attempt_cannot_submit_again_or_advance(self):
        job=self.fill();job=self.queue.claim_submit(self.qid,job['id'])
        with self.assertRaises(ValueError):self.queue.claim_submit(self.qid,job['id'])
        self.queue.stop(self.qid)
        a=self.app();a.contact_results.put(('submit',self.qid,job,submitted(job),None));a.poll_contact_worker()
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'stopped')
        self.assertEqual(self.store.next_contact_number(),12)
        self.assertIsNotNone(self.store.db.execute('SELECT report_json FROM contact_queue_submissions').fetchone()[0])

    def test_restart_after_create_claim_requires_review_without_replay(self):
        job=self.fill();self.queue.claim_submit(self.qid,job['id'])
        self.store.close();self.store=Store(self.path);self.queue=ContactQueue(self.store)
        self.assertEqual(self.queue.snapshot(self.qid)['state'],'review');self.assertIsNone(self.queue.current(self.qid))
        with self.assertRaises(ValueError):self.queue.claim_submit(self.qid,job['id'])
        self.assertEqual(self.store.next_contact_number(),12)

    def test_automatic_fill_callback_persists_attempt_before_native_create(self):
        original=self.queue.current(self.qid);job=self.queue.claim_fill(self.qid,original['id']);a=self.app()
        def native(w,script,payload):
            current=self.queue.current(self.qid)
            self.assertEqual(current['state'],'submitting');self.assertEqual(self.store.next_contact_number(),12)
            self.assertEqual(script,'submit_contact.ps1');self.assertEqual(payload,submission_payload(current))
            self.assertEqual(w,current['window'])
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM contact_queue_submissions').fetchone()[0],1)
            return submitted(current)
        a.contact_results.put(('fill',self.qid,job,filled(job),None))
        with patch('app.threading.Thread',ImmediateThread),patch('app.run_window_script',side_effect=native) as run:
            a.poll_contact_worker();a.poll_contact_worker();run.assert_called_once()
        self.assertEqual(self.queue.current(self.qid)['state'],'submitted')
        self.assertEqual(self.store.next_contact_number(),12)

    def test_post_create_failure_preserves_evidence_and_never_touches_B(self):
        job=self.fill();a=self.app()
        failure=submitted(job);failure.update(ok=False,state='review',stage='open_profile',profile_opened=False)
        with patch('app.threading.Thread',ImmediateThread),patch('app.run_window_script',side_effect=WindowActionError('profile did not open',failure)) as run:
            a.start_contact_submit(self.qid,job['id']);a.poll_contact_worker();a.tick_contact_queue();run.assert_called_once()
        s=self.queue.snapshot(self.qid);self.assertEqual(s['state'],'review');self.assertEqual(s['jobs'][1]['state'],'queued')
        self.assertEqual(self.store.next_contact_number(),12)
        path=Path(self.tmp.name)/'report.json';self.queue.export(path)
        self.assertTrue(json.loads(path.read_text())['form_submissions'][0]['report']['create_invoked'])

    def test_proven_limit_after_submit_skips_A_B_uses_same_number(self):
        job=self.fill();job=self.queue.claim_submit(self.qid,job['id']);self.finish(job,submitted(job,dialog=True))
        r=limit(job['window']);self.assertEqual(self.queue.accept(self.qid,job['id'],[r,copy.deepcopy(r)])['outcome'],'restriction')
        self.assertIsNone(self.queue.current(self.qid));self.assertEqual(self.queue.snapshot(self.qid)['state'],'review');self.assertEqual(self.store.next_contact_number(),12)
        self.assertEqual(self.queue.snapshot(self.qid)['jobs'][0]['item']['status'],'uncertain')

    def test_different_profile_instance_or_wrong_phone_never_confirms_success(self):
        job=self.fill();job=self.queue.claim_submit(self.qid,job['id']);self.finish(job,submitted(job))
        r=profile(job);r['scope_runtime_id']='other-profile'
        with self.assertRaisesRegex(ValueError,'身份'):self.queue.accept(self.qid,job['id'],[r,r])
        r=profile(job);r['controls'][0]['name']='+5516999999999'
        self.assertIsNone(self.queue.accept(self.qid,job['id'],[r,copy.deepcopy(r)]))
        self.assertEqual(self.store.next_contact_number(),12)

    def test_legacy_queue_keeps_manual_mode_and_cannot_claim_create(self):
        self.queue.stop(self.qid)
        with self.store.db:self.store.db.execute('UPDATE contact_queues SET auto_submit=0 WHERE id=?',(self.qid,));self.store.db.execute("UPDATE contact_queues SET state='running' WHERE id=?",(self.qid,))
        job=self.fill()
        self.assertFalse(self.queue.snapshot(self.qid)['contact_submit_automated'])
        with self.assertRaises(ValueError):self.queue.claim_submit(self.qid,job['id'])
        r=profile(job);self.assertEqual(self.queue.accept(self.qid,job['id'],[r,r])['outcome'],'added')

    def test_target_two_keeps_prior_A10_B11_and_adds_only_A12_B13(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Store(Path(folder)/'prior.sqlite3')
            try:
                s.import_text('phone','\n'.join('+55169912345%02d'%i for i in range(1,5)));s.set_next_number(10)
                for account in ('A','B'):
                    batch=s.create_batch(account,1);item,_=s.reserve_next(batch);s.record_add_result(item['id'],'added')
                q=ContactQueue(s);qid=q.configure([{'account':a,'window':window(i),'target':2} for i,a in enumerate(('A','B'),1)],auto_submit=True)
                q.start(qid)
                for account,number in (('A',12),('B',13)):
                    job=q.current(qid);self.assertEqual(job['account'],account)
                    job=q.claim_fill(qid,job['id']);q.finish_fill(qid,job['id'],filled(job))
                    job=q.claim_submit(qid,job['id']);self.assertEqual(job['preview_number'],number)
                    q.save_submit_result(qid,job['id'],job['item_id'],submitted(job));q.finish_submit(qid,job['id'],job['item_id'])
                    r=profile(job);q.accept(qid,job['id'],[r,r])
                self.assertEqual([j['added_numbers'] for j in q.snapshot(qid)['jobs']],[[10,12],[11,13]])
                self.assertEqual(q.snapshot(qid)['state'],'done');self.assertEqual(s.next_contact_number(),14)
            finally:s.close()

    def test_timeout_or_invalid_native_json_does_not_claim_create_was_not_sent(self):
        import controls_probe,os,subprocess
        w=window(1)
        fake_os=SimpleNamespace(name='nt',environ=os.environ)
        outcomes=(subprocess.TimeoutExpired('powershell',30),SimpleNamespace(stdout=b'invalid',stderr=b'provider failed',returncode=1))
        for outcome in outcomes:
            kwargs={'side_effect':outcome} if isinstance(outcome,Exception) else {'return_value':outcome}
            with patch.object(controls_probe,'os',fake_os),patch('controls_probe.scan_windows',return_value=[w]),patch('pathlib.Path.is_file',return_value=True),patch.object(subprocess,'CREATE_NO_WINDOW',0,create=True),patch('controls_probe.subprocess.run',**kwargs):
                with self.assertRaisesRegex(RuntimeError,'Create 可能已执行'):
                    controls_probe.run_window_script(w,'submit_contact.ps1',{'number':'12'})


if __name__=='__main__':unittest.main()
