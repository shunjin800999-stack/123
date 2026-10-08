import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

from controls_probe import WindowActionError,inspect_controls
from username_contact import profile_number_verified
from username_number_read import read_username_number
import test_username_contact as username_cases


class Clock:
    def __init__(self):self.value=0.;self.waits=[]
    def now(self):return self.value
    def sleep(self,seconds):self.waits.append(seconds);self.value+=seconds


def report(title='1395',*,errors=None,runtime_id='profile-1395'):
    return {'ok':True,'read_only':True,'scope':'profile','scope_class':'class Info::Profile::Widget',
        'scope_runtime_id':runtime_id,'window_handle':101,'process_id':201,'truncated':False,
        'errors':errors or [],'controls':[{'type':'Text','name':title,'class_name':'class Ui::MarqueeLabel','visible':True}]}


class UsernameNumberReadTests(unittest.TestCase):
    window={'hwnd':101,'pid':201}

    def run_read(self,reader,number=1395):
        clock=Clock()
        result=read_username_number(self.window,number,reader,sleep=clock.sleep,clock=clock.now)
        return result,clock

    def test_ready_number_has_one_read_and_no_sleep(self):
        read=Mock(return_value=report());r,c=self.run_read(read)
        self.assertTrue(profile_number_verified(r,self.window,1395))
        self.assertEqual(read.call_count,1);self.assertEqual(c.waits,[])

    def test_replay_1395_first_child_failure_then_number_updates(self):
        bad=json.loads((Path(__file__).parent/'test_fixtures/username-number-read-1395.json').read_text(encoding='utf-8'))
        read=Mock(side_effect=[bad,report()]);r,c=self.run_read(read)
        self.assertEqual(read.call_count,2);self.assertEqual(c.waits,[.3])
        self.assertTrue(profile_number_verified(r,self.window,1395))
        self.assertEqual(r['number_read_retry']['history'][0]['errors'],bad['errors'])
        self.assertEqual(bad['controls'][0]['name'],'Mouraa')
        self.assertTrue(bad['errors'])

    def test_stale_title_waits_for_expected_number_without_navigation(self):
        read=Mock(side_effect=[report('Mouraa'),report('Mouraa'),report()]);r,c=self.run_read(read)
        self.assertEqual(read.call_count,3);self.assertAlmostEqual(sum(c.waits),.6)
        self.assertEqual(r['number_read_retry']['outcome'],'verified')

    def test_number_with_header_read_error_cannot_confirm_until_clean(self):
        read=Mock(side_effect=[report(errors=['title unavailable']),report()]);r,c=self.run_read(read)
        self.assertEqual(read.call_count,2)
        self.assertEqual(r['errors'],[])

    def test_persistent_errors_are_bounded_and_never_erased(self):
        read=Mock(return_value=report(errors=['header unavailable']));r,c=self.run_read(read)
        self.assertLessEqual(read.call_count,11);self.assertLessEqual(sum(c.waits),3.001)
        self.assertEqual(r['errors'],['header unavailable'])
        self.assertFalse(profile_number_verified(r,self.window,1395))

    def test_changed_window_or_popup_is_not_retried(self):
        for changes in ({'window_handle':102},{'process_id':202},{'scope':'dialog'},
                        {'scope_class':'class DifferentProfile'},{'read_only':False}):
            read=Mock(return_value=dict(report(),**changes));r,c=self.run_read(read)
            self.assertEqual(read.call_count,1);self.assertEqual(c.waits,[])
            self.assertFalse(profile_number_verified(r,self.window,1395))

    def test_changed_profile_cannot_confirm_even_if_it_contains_number(self):
        read=Mock(side_effect=[report('Mouraa'),report(runtime_id='another-profile')])
        with self.assertRaisesRegex(WindowActionError,'身份改变'):
            self.run_read(read)
        self.assertEqual(read.call_count,2)

    def test_duplicates_hidden_or_similar_numbers_do_not_confirm(self):
        cases=[report('13950'),report('E 1395'),report()]
        cases[2]['controls'][0]['visible']=False
        duplicate=report();duplicate['controls'].append(copy.deepcopy(duplicate['controls'][0]));cases.append(duplicate)
        for bad in cases:
            read=Mock(side_effect=[bad,report()]);r,c=self.run_read(read)
            self.assertEqual(read.call_count,2)
            self.assertTrue(profile_number_verified(r,self.window,1395))

    def test_profile_appearing_after_reopen_can_be_read(self):
        window=dict(report(),scope='window',scope_class='class MainWindow',scope_runtime_id='root',controls=[])
        read=Mock(side_effect=[window,report()]);r,c=self.run_read(read)
        self.assertEqual(read.call_count,2)
        self.assertTrue(profile_number_verified(r,self.window,1395))

    def test_transport_or_binding_exception_is_not_swallowed(self):
        read=Mock(side_effect=RuntimeError('window changed'))
        with self.assertRaisesRegex(RuntimeError,'window changed'):self.run_read(read)
        self.assertEqual(read.call_count,1)

    def test_number_probe_is_opt_in_and_normal_diagnostics_keep_payload(self):
        with patch('controls_probe.run_window_script',return_value=report()) as run:
            inspect_controls(self.window)
            self.assertIsNone(run.call_args.args[2])
            inspect_controls(self.window,queue_probe=True)
            self.assertEqual(run.call_args.args[2],{'queue_probe':True})
            inspect_controls(self.window,profile_number_only=True)
            self.assertEqual(run.call_args.args[2],{'queue_probe':True,'profile_number_only':True})


class UsernameNumberQueueTests(unittest.TestCase):
    def setUp(self):
        self.case=username_cases.UsernameContactTests()
        self.case.setUp();self.addCleanup(self.case.tearDown)
        job,r=self.case.refreshed_submission()
        r.update(submit_method='verified_modal_done_mouse_sendinput_number_only',number_only_verification=True,
            submit_form_preserved_until_exit=True,profile_reopened=True)
        self.case.queue.save_submit_result(self.case.qid,job['id'],job['item_id'],r)
        self.case.queue.finish_submit(self.case.qid,job['id'],job['item_id'])
        self.job=self.case.queue.current(self.case.qid);self.app=self.case.app();self.clock=Clock()

    def saved(self):return report(str(self.job['preview_number']))

    def assert_one_done(self):
        self.assertEqual(self.case.store.db.execute('SELECT COUNT(*) FROM contact_queue_submissions').fetchone()[0],1)

    def test_initial_numeric_read_retries_then_advances_once_without_reopen(self):
        bad=report('Mouraa',errors=['GetFirstChild failed'])
        with patch('app.threading.Thread',username_cases.ImmediateThread), \
             patch('app.inspect_controls',side_effect=[bad,self.saved()]) as read, \
             patch('username_number_read.time.sleep',side_effect=self.clock.sleep), \
             patch('username_number_read.time.monotonic',side_effect=self.clock.now), \
             patch.object(self.app,'start_saved_username_close') as close, \
             patch.object(self.app,'start_submitted_username_refresh') as reopen, \
             patch('app.run_window_script') as native:
            self.app.drive_contact_queue();self.app.poll_contact_worker()
        self.assertEqual(read.call_count,2)
        self.assertTrue(all(c.kwargs['profile_number_only'] for c in read.call_args_list))
        reopen.assert_not_called();native.assert_not_called();close.assert_called_once()
        self.assertEqual(self.case.store.next_contact_number(),255)
        self.assert_one_done()

    def test_ready_queue_number_does_not_wait_or_read_twice(self):
        with patch('app.threading.Thread',username_cases.ImmediateThread), \
             patch('app.inspect_controls',return_value=self.saved()) as read, \
             patch('username_number_read.time.sleep',side_effect=self.clock.sleep), \
             patch.object(self.app,'start_saved_username_close'):
            self.app.drive_contact_queue();self.app.poll_contact_worker()
        self.assertEqual(read.call_count,1);self.assertEqual(self.clock.waits,[])
        self.assertEqual(self.case.store.next_contact_number(),255);self.assert_one_done()

    def test_reopened_profile_retries_reads_without_reopening_again_or_done(self):
        with patch('app.threading.Thread',username_cases.ImmediateThread), \
             patch('app.inspect_controls',side_effect=[report('Mouraa'),self.saved()]) as read, \
             patch('username_number_read.time.sleep',side_effect=self.clock.sleep), \
             patch('username_number_read.time.monotonic',side_effect=self.clock.now), \
             patch('app.run_window_script',return_value={'ok':True,'state':'profile_reopened'}) as native, \
             patch.object(self.app,'start_saved_username_close') as close:
            self.app.start_submitted_username_refresh(self.case.qid,self.job)
            self.app.poll_contact_worker()
        self.assertEqual(native.call_count,1)
        self.assertEqual(native.call_args.args[2]['mode'],'reopen_submitted')
        self.assertEqual(read.call_count,2);close.assert_called_once()
        self.assertEqual(self.case.store.next_contact_number(),255);self.assert_one_done()

    def test_persistent_read_error_keeps_number_and_reserved_item(self):
        with patch('app.threading.Thread',username_cases.ImmediateThread), \
             patch('app.inspect_controls',return_value=report(errors=['header unavailable'])) as read, \
             patch('username_number_read.time.sleep',side_effect=self.clock.sleep), \
             patch('username_number_read.time.monotonic',side_effect=self.clock.now), \
             patch('app.run_window_script') as native:
            self.app.drive_contact_queue();self.app.poll_contact_worker()
        self.assertGreater(read.call_count,1);self.assertLessEqual(read.call_count,11)
        self.assertEqual(self.case.queue.snapshot(self.case.qid)['state'],'review')
        self.assertEqual(self.case.store.next_contact_number(),254)
        self.assertEqual(next(r for r in self.case.store.rows() if r['id']==self.job['item_id'])['status'],'reserved')
        native.assert_not_called();self.assert_one_done()

    def test_paused_numeric_submission_recovers_with_one_read_without_done(self):
        self.case.queue.stop(self.case.qid,'UIA read failed',review=True)
        with patch('app.threading.Thread',username_cases.ImmediateThread), \
             patch('app.inspect_controls',side_effect=[report('Mouraa',errors=['GetFirstChild failed']),self.saved()]) as read, \
             patch('username_number_read.time.sleep',side_effect=self.clock.sleep), \
             patch('username_number_read.time.monotonic',side_effect=self.clock.now), \
             patch('app.run_window_script',return_value={'ok':True,'state':'profile_reopened'}) as native, \
             patch.object(self.app,'start_saved_username_close') as close:
            self.app.recover_submitted_profile();self.app.poll_contact_worker()
        self.assertEqual(read.call_count,2);close.assert_called_once()
        self.assertEqual(native.call_count,1)
        self.assertEqual(native.call_args.args[2]['mode'],'reopen_submitted')
        self.assertEqual(self.case.store.next_contact_number(),255)
        self.assertEqual(self.case.queue.snapshot(self.case.qid)['state'],'running')
        self.assert_one_done()

    def test_paused_numeric_recovery_needs_complete_original_done_proof(self):
        self.case.queue.stop(self.case.qid,'UIA read failed',review=True)
        row=self.case.store.db.execute('SELECT report_json FROM contact_queue_submissions').fetchone()
        proof=json.loads(row[0]);proof['create_runtime_id']=''
        with self.case.store.db:
            self.case.store.db.execute('UPDATE contact_queue_submissions SET report_json=?',(json.dumps(proof),))
        with self.assertRaises(ValueError):
            self.case.queue.recover_submitted_profile(self.case.qid,self.job['id'],[self.saved()])
        self.assertEqual(self.case.store.next_contact_number(),254)
        self.assert_one_done()

    def test_paused_numeric_recovery_rejects_wrong_number_without_advancing(self):
        self.case.queue.stop(self.case.qid,'UIA read failed',review=True)
        with self.assertRaisesRegex(ValueError,'数字备注核验未通过'):
            self.case.queue.recover_submitted_profile(self.case.qid,self.job['id'],[report('253')])
        self.assertEqual(self.case.store.next_contact_number(),254)
        self.assertEqual(self.case.queue.snapshot(self.case.qid)['state'],'review')
        self.assert_one_done()


if __name__=='__main__':unittest.main()
