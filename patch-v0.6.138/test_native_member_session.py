import io,json,queue,subprocess,unittest
from unittest.mock import Mock,patch
from native_member_session import NativeMemberSession

class NativeSessionTests(unittest.TestCase):
    def fake(self):
        s=NativeMemberSession();p=Mock();p.poll.return_value=None;p.pid=77
        p.stdin=io.StringIO();p.stdout=io.StringIO();s.process=p;s.responses=queue.Queue()
        def flush():
            r=json.loads(p.stdin.getvalue().splitlines()[-1])
            s.responses.put(json.dumps({'request_id':r['request_id'],
                'stdout':json.dumps({'ok':True,'window_handle':int(r['hwnd']),'number':r['payload']['number']}),
                'stderr':'','returncode':0,'native_seconds':.1}))
        p.stdin.flush=flush
        return s,p
    def call(self,s,hwnd='1',number='24'):
        return s.run('powershell',{'TG_INSPECT_HWND':hwnd,'TG_INSPECT_PID':'2'},'select_member.ps1',{'number':number,'mode':'prepare'},1)
    def test_rebind_accounts_and_payload_without_restarting(self):
        s,p=self.fake()
        with patch.object(s,'start') as start:a=self.call(s);b=self.call(s,'3','44')
        start.assert_not_called();self.assertTrue(b.member_timing['reused'])
        self.assertEqual(json.loads(a.stdout)['window_handle'],1)
        self.assertEqual(json.loads(b.stdout)['window_handle'],3);self.assertEqual(json.loads(b.stdout)['number'],'44')
        tokens=[json.loads(x)['request_id'] for x in p.stdin.getvalue().splitlines()]
        self.assertNotEqual(tokens[0],tokens[1]);s.close()
    def test_wrong_token_discards_worker_no_retry(self):
        s,p=self.fake();s.responses.put('{"request_id":"old"}')
        with self.assertRaises(ValueError):self.call(s)
        self.assertIsNone(s.process);p.kill.assert_called_once()
    def test_failure_preserves_uncertain_click_then_discards_worker(self):
        s,p=self.fake()
        def flush():
            r=json.loads(p.stdin.getvalue().splitlines()[-1])
            s.responses.put(json.dumps({'request_id':r['request_id'],'stdout':'{"ok":false,"click_sent":true}',
                'stderr':'','returncode':0,'native_seconds':.1}))
        p.stdin.flush=flush;v=self.call(s)
        self.assertTrue(json.loads(v.stdout)['click_sent']);self.assertIsNone(s.process);p.kill.assert_called_once()
    def test_timeout_no_replay(self):
        s,p=self.fake()
        with patch.object(s.responses,'get',side_effect=queue.Empty),patch.object(s,'start') as start:
            with self.assertRaises(subprocess.TimeoutExpired):self.call(s)
        start.assert_not_called();p.kill.assert_called_once();self.assertIsNone(s.process)
    def test_exit_stops_without_retry(self):
        s,p=self.fake();s.responses.put(None)
        with self.assertRaises(RuntimeError):self.call(s)
        self.assertIsNone(s.process);p.kill.assert_called_once()
    def test_unrelated_operation_never_sent(self):
        s,p=self.fake()
        with self.assertRaises(ValueError):s.run('ps',{},'submit_contact.ps1',{},1)
        self.assertEqual(p.stdin.getvalue(),'');s.close()

    def test_navigation_cleanup_and_group_reads_share_process_and_rebind_window(self):
        s,p=self.fake()
        with patch.object(s,'start') as start:
            for script,hwnd in [('prepare_group_page.ps1','1'),('inspect_groups.ps1','1'),('navigate_group.ps1','3')]:
                result=s.run('ps',{'TG_INSPECT_HWND':hwnd,'TG_INSPECT_PID':'2'},script,{'number':'24'},1)
                self.assertEqual(json.loads(result.stdout)['window_handle'],int(hwnd))
                self.assertEqual(result.member_timing['worker_pid'],77)
        start.assert_not_called();s.close()

    def test_contact_inspection_and_close_rebind_without_cached_profile(self):
        s,p=self.fake()
        with patch.object(s,'start') as start:
            for script,hwnd,number in [('inspect_controls.ps1','1','84'),
                                      ('close_contact_profile.ps1','1','84'),
                                      ('inspect_controls.ps1','3','89')]:
                result=s.run('ps',{'TG_INSPECT_HWND':hwnd,'TG_INSPECT_PID':'2'},script,{'number':number},1)
                self.assertEqual(json.loads(result.stdout)['window_handle'],int(hwnd))
                self.assertEqual(json.loads(result.stdout)['number'],number)
        start.assert_not_called()
        self.assertEqual(len(p.stdin.getvalue().splitlines()),3)
        s.close()

    def test_username_worker_rebinds_modes_and_accounts_without_restarting(self):
        s,p=self.fake();s.username=True;s.worker_name='native_username_worker.ps1'
        with patch.object(s,'start') as start:
            for mode,hwnd,number in [('open','1','24'),('fill','1','24'),('submit','1','24'),('close_saved','1','24'),('open','3','25')]:
                result=s.run('ps',{'TG_INSPECT_HWND':hwnd,'TG_INSPECT_PID':'2'},'username_contact.ps1',{'mode':mode,'number':number},1)
                self.assertEqual(json.loads(result.stdout)['window_handle'],int(hwnd))
                self.assertEqual(result.member_timing['transport'],'persistent_username_worker')
        start.assert_not_called()
        requests=[json.loads(x) for x in p.stdin.getvalue().splitlines()]
        self.assertEqual(len(set(x['request_id'] for x in requests)),5)
        self.assertEqual(requests[-1]['payload']['mode'],'open')
        self.assertEqual(requests[-1]['hwnd'],'3')
        s.close()

    def test_username_worker_rejects_other_operations_and_discards_failed_submit(self):
        s,p=self.fake();s.username=True
        with self.assertRaises(ValueError):self.call(s)
        self.assertEqual(p.stdin.getvalue(),'')
        def flush():
            r=json.loads(p.stdin.getvalue().splitlines()[-1])
            s.responses.put(json.dumps({'request_id':r['request_id'],'stdout':'{"ok":false,"create_invoked":true}',
                                        'stderr':'','returncode':0,'native_seconds':.1}))
        p.stdin.flush=flush
        result=s.run('ps',{'TG_INSPECT_HWND':'1','TG_INSPECT_PID':'2'},'username_contact.ps1',{'mode':'submit'},1)
        self.assertTrue(json.loads(result.stdout)['create_invoked'])
        self.assertIsNone(s.process);p.kill.assert_called_once()
