import base64
import threading
import unittest
from unittest.mock import Mock,patch

from task_notifications import TaskNotifier,play_notification


class NotificationTests(unittest.TestCase):
    def notifier(self):
        player=Mock();return TaskNotifier(player),player

    def complete(self,notifier):notifier._pending.join()

    def test_loaded_previous_stop_does_not_play_then_current_failure_plays_once(self):
        notifier,player=self.notifier();snapshot={'id':1,'state':'review','jobs':[]}
        notifier.observe_addition(snapshot);player.assert_not_called()
        notifier.arm_addition(1);notifier.observe_addition(snapshot);notifier.observe_addition(snapshot)
        self.complete(notifier);player.assert_called_once_with('stopped')

    def test_finished_and_resuming_the_same_plan_can_notify_again(self):
        notifier,player=self.notifier();snapshot={'id':1,'state':'done','jobs':[{'state':'target_reached'},{'state':'skipped_account'}]}
        for _ in (1,2):
            notifier.arm_addition(1);notifier.observe_addition(snapshot);notifier.observe_addition(snapshot)
        self.complete(notifier);self.assertEqual(player.call_count,2);self.assertTrue(all(c.args==('finished',) for c in player.call_args_list))

    def test_exhausted_or_restricted_plan_requests_check_even_when_queue_says_done(self):
        for state in ('no_list','paused'):
            notifier,player=self.notifier();notifier.arm_addition(1)
            notifier.observe_addition({'id':1,'state':'done','jobs':[{'state':state}]})
            self.complete(notifier);player.assert_called_once_with('stopped')

    def test_manual_stop_can_disarm_without_alert(self):
        notifier,player=self.notifier();notifier.observe_addition({'id':1,'state':'running','jobs':[]})
        notifier.disarm_addition(1);notifier.observe_addition({'id':1,'state':'stopped','jobs':[]})
        player.assert_not_called()

    def test_audio_does_not_block_or_run_on_ui_thread(self):
        entered=threading.Event();release=threading.Event();identifiers=[]
        def player(kind):
            identifiers.append(threading.get_ident());entered.set();release.wait(2)
        notifier=TaskNotifier(player);notifier.emit('finished')
        self.assertTrue(entered.wait(1));self.assertNotEqual(identifiers[0],threading.get_ident())
        release.set();self.complete(notifier)

    def test_audio_error_does_not_stop_later_notifications(self):
        notifier,player=self.notifier();player.side_effect=[OSError('fixture'),None]
        notifier.emit('stopped');notifier.emit('finished');self.complete(notifier)
        self.assertEqual(player.call_count,2)

    def test_windows_encoded_command_contains_fixed_chinese_text_and_hidden_window(self):
        with patch('task_notifications.os.name','nt'),patch('task_notifications.subprocess.run') as run:
            play_notification('stopped')
        args=run.call_args.args[0];script=base64.b64decode(args[-1]).decode('utf-16le')
        self.assertIn("Speak('已停止，请检查')",script);self.assertIn('SystemSounds',script)
        self.assertEqual(run.call_args.kwargs['timeout'],20);self.assertNotIn('shell',run.call_args.kwargs)

    def test_non_windows_does_not_launch_audio_process(self):
        with patch('task_notifications.os.name','posix'),patch('task_notifications.subprocess.run') as run:
            play_notification('finished')
        run.assert_not_called()
