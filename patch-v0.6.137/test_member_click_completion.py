"""Keep both OCR checks; continue only for a proven matched, single click."""
import ast
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_target_only_members as fixtures
from member_batch import select_members_test
from member_click_completion import confirmed_target_click, can_continue_after_header_failure
from window_queue import ready_for_manual_invite


class ClickCompletionTests(unittest.TestCase):
    def replay(self,**options):
        return fixtures.TargetOnlyTests().replay(continue_on_click=True,**options)

    def test_successful_first_read_keeps_verification_and_has_no_retry(self):
        r,calls,_,wait=self.replay()
        self.assertTrue(r['ok']);self.assertTrue(r['selection_verified'])
        self.assertTrue(r['completion_confirmed']);self.assertEqual(r['completion_basis'],'visual_selection')
        self.assertEqual(len(r['verification_reads']),1);self.assertNotIn('post_click_retry',r)
        self.assertEqual([mode for mode,_ in calls],['prepare','click_once','inspect_member_visual.ps1'])
        wait.assert_called_once_with(.2)

    def test_successful_retry_keeps_actual_visual_proof(self):
        r,calls,_,_=self.replay(texts=('736','737'))
        self.assertTrue(r['selection_verified']);self.assertTrue(r['post_click_retry']['verified'])
        self.assertEqual(len(r['verification_reads']),2);self.assertEqual(r['completion_basis'],'visual_selection')
        self.assertEqual(sum(mode=='click_once' for mode,_ in calls),1)

    def test_avatar_v_below80_after_both_reads_continues_without_claiming_selected(self):
        r,calls,_,wait=self.replay(texts=('V','V'),scores=(.31422,.31422))
        self.assertTrue(r['ok'],r['reason']);self.assertTrue(r['completion_confirmed'])
        self.assertFalse(r['selection_verified']);self.assertFalse(r['post_click_retry']['verified'])
        self.assertEqual(r['state'],'clicked_unverified')
        self.assertEqual(r['completion_basis'],'confirmed_click_after_two_failed_reads')
        self.assertEqual(len(r['verification_reads']),2)
        self.assertTrue(confirmed_target_click(r));self.assertTrue(can_continue_after_header_failure(r))
        self.assertEqual(sum(mode=='click_once' for mode,_ in calls),1)
        self.assertEqual(sum(mode=='inspect_member_visual.ps1' for mode,_ in calls),2)
        self.assertFalse(r['final_invite_clicked']);wait.assert_called_once_with(.2)

    def test_two_missing_target_reads_can_continue(self):
        r,_,_,_=self.replay(texts=('736','736'))
        self.assertTrue(r['ok']);self.assertFalse(r['selection_verified'])

    def test_two_empty_ocr_reads_can_continue(self):
        r,_,_,_=self.replay(texts=('',''))
        self.assertTrue(r['ok']);self.assertFalse(r['selection_verified'])

    def test_disabled_policy_still_stops_for_the_same_two_failed_reads(self):
        r,_,_,_=fixtures.TargetOnlyTests().replay(texts=('V','V'),scores=(.31,.31))
        self.assertFalse(r['ok']);self.assertEqual(r['state'],'review')

    def test_wrong_search_result_never_clicks_or_continues(self):
        r,calls,_,_=self.replay(names=('7370','E 737','737 bot'))
        self.assertFalse(r['ok']);self.assertFalse(r['completion_confirmed'])
        self.assertFalse(r['click_sent']);self.assertEqual([mode for mode,_ in calls],['prepare'])

    def test_unconfirmed_query_never_clicks(self):
        r,_,_,_=self.replay(mutate_before=lambda r:r['search_preparation'].update(value_matches=False))
        self.assertFalse(r['ok']);self.assertFalse(r['completion_confirmed']);self.assertFalse(r['click_requested'])

    def test_missing_click_receipt_cannot_bypass_ocr_failure(self):
        for field,value in (('ok',False),('mode','prepare'),('number','738'),
                ('click_attempted',False),('click_sent',False),('x',0),('y',0),('final_invite_clicked',True)):
            with self.subTest(field=field):
                r,_,_,_=self.replay(texts=('V','V'),scores=(.31,.31),
                    mutate_click=lambda r:r.update({field:value}))
                self.assertFalse(r['ok']);self.assertFalse(r['completion_confirmed'])
                self.assertFalse(r['click_confirmed']);self.assertNotIn('verification_reads',r)

    def test_window_or_dialog_change_still_stops_without_a_second_read(self):
        for field,value in (('process_id',201),('window_handle',101),('dialog_runtime_id','other')):
            with self.subTest(field=field):
                r,_,_,_=self.replay(texts=('V','V'),scores=(.31,.31),
                    mutate_after=lambda r:r.update({field:value}))
                self.assertFalse(r['ok']);self.assertEqual(len(r['verification_reads']),1)
                self.assertFalse(r['completion_confirmed'])

    def test_changed_capture_cannot_complete_by_click(self):
        r,_,_,_=self.replay(texts=('V','V'),scores=(.31,.31),
            mutate_after=lambda r:r['capture'].update(left=r['capture']['left']+1))
        self.assertFalse(r['ok']);self.assertFalse(r['completion_confirmed'])

    def test_manual_pause_wins_even_after_confirmed_click(self):
        r,calls,_,_=self.replay(texts=('V','V'),scores=(.31,.31),pause=True)
        self.assertEqual(r['state'],'paused');self.assertFalse(r['completion_confirmed'])
        self.assertEqual(len(r['verification_reads']),1)
        self.assertEqual(sum(mode=='click_once' for mode,_ in calls),1)

    def test_invalid_ocr_transport_does_not_become_click_completion(self):
        r,_,_,_=self.replay(texts=('V','V'),scores=(.31,.31))
        for change in (lambda s:s['verification_reads'][0]['report']['backup_ocr'].update(ok=False),
                       lambda s:s['verification_reads'][0]['report']['selection_header_ocr'].update(error='截图校验不一致'),
                       lambda s:s.update(verification_reads=s['verification_reads'][:1]),
                       lambda s:s['verification_reads'][1]['report'].update(dialog_runtime_id='other'),
                       lambda s:s['post_click_retry'].update(capture_requested=False)):
            changed=copy.deepcopy(r);change(changed)
            self.assertFalse(can_continue_after_header_failure(changed))

    def test_stale_plan_or_wrong_target_cannot_become_click_completion(self):
        r,_,_,_=self.replay(texts=('V','V'),scores=(.31,.31))
        for change in (lambda s:s['plan'].update(x=0),
                       lambda s:s['before']['header_scroll'].update(search_value='738'),
                       lambda s:s.update(expected_selected=['737']),
                       lambda s:s.update(click_requested=False),
                       lambda s:s['before']['search_preparation'].update(number='738')):
            changed=copy.deepcopy(r);change(changed)
            self.assertFalse(confirmed_target_click(changed))

    def test_gui_both_groups_enable_retry_then_click_fallback(self):
        tree=ast.parse((Path(__file__).parent/'app.py').read_text(encoding='utf-8-sig'))
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Name) and n.func.id=='select_pinned_member_queue']
        self.assertEqual(len(calls),1)
        keywords={k.arg:k.value for k in calls[0].keywords}
        self.assertTrue(keywords['continue_on_click'].value)
        self.assertTrue(keywords['target_only'].value);self.assertTrue(keywords['fast_visible'].value)


class ClickCompletionBatchTests(unittest.TestCase):
    def batch(self,step):
        initial=fixtures.frame();initial['analysis']={'usable':True,'selected_numbers':[],'other_header_words':[]}
        with tempfile.TemporaryDirectory() as folder,\
             patch('member_batch.inspect_member_search',return_value=initial),\
             patch('backup_ocr.apply_visible_header',return_value=initial),\
             patch('visual_members.analyze_member_visual',return_value=initial['analysis']),\
             patch('member_batch.select_member_test',return_value=step) as select,\
             patch('member_batch.inspect_final_member') as final:
            result=select_members_test(fixtures.WINDOW,Path(folder)/'batch.json',['737'],
                fast_visible=True,target_only=True,continue_on_click=True)
        self.assertTrue(select.call_args.kwargs['continue_on_click']);final.assert_not_called()
        return result

    def test_last_unverified_click_finishes_without_extra_final_capture(self):
        step=fixtures.TargetOnlyTests().replay(continue_on_click=True,ledger=(),texts=('V','V'),scores=(.31,.31))[0]
        r=self.batch(step)
        self.assertTrue(r['ok']);self.assertTrue(r['completion_confirmed']);self.assertFalse(r['selection_verified'])
        self.assertEqual(r['selected_numbers'],[]);self.assertEqual(r['completed_numbers'],['737'])
        self.assertEqual(r['click_confirmed_numbers'],['737']);self.assertEqual(r['unverified_numbers'],['737'])
        self.assertEqual(r['remaining_numbers'],[]);self.assertEqual(r['final_reads'],[])
        self.assertTrue(ready_for_manual_invite(r,fixtures.WINDOW,['737']))

    def test_last_visually_verified_click_still_records_selected(self):
        step=fixtures.TargetOnlyTests().replay(continue_on_click=True,ledger=())[0]
        r=self.batch(step)
        self.assertTrue(r['selection_verified']);self.assertEqual(r['selected_numbers'],['737'])
        self.assertEqual(r['unverified_numbers'],[]);self.assertTrue(ready_for_manual_invite(r,fixtures.WINDOW,['737']))

    def test_unverified_click_cannot_be_declared_visual_success(self):
        step=fixtures.TargetOnlyTests().replay(continue_on_click=True,ledger=(),texts=('V','V'),scores=(.31,.31))[0]
        r=self.batch(step)
        for change in (lambda r:r.update(selection_verified=True),
                       lambda r:r.update(selected_numbers=['737']),
                       lambda r:r.update(unverified_numbers=[]),
                       lambda r:r.update(completed_numbers=[]),
                       lambda r:r.update(click_confirmed_numbers=[]),
                       lambda r:r['steps'][0].update(selection_verified=True),
                       lambda r:r['steps'][0]['click'].update(click_attempted=False),
                       lambda r:r.update(steps=[]),
                       lambda r:r['initial'].update(dialog_runtime_id='other'),
                       lambda r:r.update(completion_confirmed=False)):
            changed=copy.deepcopy(r);change(changed)
            self.assertFalse(ready_for_manual_invite(changed,fixtures.WINDOW,['737']))

    def test_unknown_click_receipt_stops_and_keeps_target_remaining(self):
        step=fixtures.TargetOnlyTests().replay(continue_on_click=True,ledger=(),texts=('V','V'),scores=(.31,.31),
            mutate_click=lambda r:r.update(click_sent=False))[0]
        r=self.batch(step)
        self.assertFalse(r['ok']);self.assertFalse(r['completion_confirmed'])
        self.assertEqual(r['completed_numbers'],[]);self.assertEqual(r['remaining_numbers'],['737'])

    def test_group1_continues_after_failure_in_first_account_and_saves_it(self):
        with tempfile.TemporaryDirectory() as folder:
            r,calls,_,saved=fixtures.TargetOnlyBatchTests().run_accounts(folder,bad_number='736',continue_on_click=True)
        self.assertTrue(r['ok'],r['reason']);self.assertTrue(r['completion_confirmed']);self.assertFalse(r['selection_verified'])
        self.assertEqual([j['state'] for j in r['jobs']],['selection_finished','selection_finished'])
        self.assertEqual(saved['selection']['unverified_numbers'],['736'])
        self.assertEqual(saved['selection']['completed_numbers'],['735','736','737'])
        self.assertEqual(saved['selection']['selected_numbers'],['735','737'])
        self.assertEqual(sum(mode=='click_once' for _,mode,_ in calls),6)
        self.assertEqual(sum(mode=='inspect_member_visual.ps1' for _,mode,_ in calls),7)
        self.assertFalse(r['contact_database_updated']);self.assertFalse(r['final_invite_clicked'])
        for job in r['jobs']:self.assertTrue(ready_for_manual_invite(job['selection'],job['window'],job['numbers']))

    def test_group2_continues_after_failure_in_second_account(self):
        with tempfile.TemporaryDirectory() as folder:
            r,calls,_,saved=fixtures.TargetOnlyBatchTests().run_accounts(folder,bad_number='739',continue_on_click=True,slot=2)
        self.assertTrue(r['ok'],r['reason']);self.assertEqual(r['slot'],2)
        self.assertEqual(saved['slot'],2);self.assertTrue(saved['selection']['selection_verified'])
        self.assertEqual(r['jobs'][1]['selection']['unverified_numbers'],['739'])
        self.assertEqual(r['jobs'][1]['selection']['completed_numbers'],['738','739','740'])
        self.assertTrue(any(number=='740' for _,_,number in calls));self.assertEqual(sum(mode=='click_once' for _,mode,_ in calls),6)

    def test_normal_multi_account_plan_uses_only_one_read_per_target(self):
        with tempfile.TemporaryDirectory() as folder:
            r,calls,_,_=fixtures.TargetOnlyBatchTests().run_accounts(folder,continue_on_click=True)
        self.assertTrue(r['ok'],r['reason']);self.assertTrue(r['selection_verified'])
        self.assertEqual(sum(mode=='inspect_member_visual.ps1' for _,mode,_ in calls),6)
        self.assertEqual(r['unverified_numbers_by_account'],[])


if __name__=='__main__':unittest.main()
