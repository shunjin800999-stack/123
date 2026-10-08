import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_member_selection as single_tests
import test_visual_members as visual_tests
from controls_probe import inspect_member_search
from visual_members import analyze_member_visual, plan_member_selection, verify_member_selection, parse_member_labels
from member_batch import select_members_test, inspect_initial_members


def report(selected=(), target=None):
    r=single_tests.live_report()
    helper=visual_tests.VisualMemberTests()
    r['regions']['search']['left']=760;r['regions']['search']['width']=190
    r['ocr']['words']=[helper.word(label,645+30*i,265) for i,label in enumerate(selected)]
    if target is not None:r['ocr']['words'].append(helper.word(target,675,320))
    r['analysis']=analyze_member_visual(r,target or '1')
    r['image_path']='test.png'
    return r


def initial_report_data(r):
    r=copy.deepcopy(r)
    a=r['analysis']
    r.update(initial_stable=a['usable'] and not a['other_header_words'],
        initial_search_applied=False,initial_reads=[])
    return r


def initial_report(selected=(),target=None):
    return initial_report_data(report(selected,target))


class MemberBatchTests(unittest.TestCase):
    def setUp(self):
        timer=patch('member_batch.time.sleep');timer.start();self.addCleanup(timer.stop)

    def test_navigation_dialog_guard_prevents_even_magnifier_fallback_search(self):
        r=report();r['analysis'].update(usable=True,selected_numbers=[],other_header_words=['p'])
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_member_visual',return_value=r),\
                    patch('member_batch.inspect_member_search') as search,\
                    patch('member_batch.select_member_test') as click:
                result=select_members_test({'hwnd':r['window_handle'],'pid':r['process_id']},
                    Path(tmp)/'guard.json',['1'],expected_dialog_id='different')
        self.assertEqual(result['state'],'review');search.assert_not_called();click.assert_not_called()

    def test_navigation_guard_is_forwarded_to_native_search_preparation(self):
        r=report();r['analysis'].update(usable=True,selected_numbers=[],other_header_words=['p'])
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_member_visual',side_effect=[r,report(),report()]),\
                    patch('member_batch.inspect_member_search',return_value=report()) as search:
                value=inspect_initial_members({'hwnd':r['window_handle'],'pid':r['process_id']},
                    Path(tmp)/'guard.png','1',expected_dialog_id=r['dialog_runtime_id'])
        self.assertTrue(value['initial_stable'])
        self.assertEqual(search.call_args.kwargs['expected_dialog_id'],r['dialog_runtime_id'])

    def test_initial_pair_is_read_without_changing_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_member_visual',return_value=report(['1','2'])) as reads,\
                 patch('member_batch.inspect_member_search') as search:
                r=inspect_initial_members({},Path(tmp)/'initial.png','1')
            self.assertTrue(r['initial_stable']);self.assertEqual(reads.call_count,3)
            search.assert_not_called();self.assertFalse(r['initial_search_applied'])
            self.assertEqual(r['analysis']['selected_numbers'],['1','2'])
            self.assertEqual(len(r['initial_reads']),3)

    def test_initial_inconsistent_reads_require_last_two_complete_matches(self):
        for values,expected in (([['1'],['1','2'],['1','2']],True),
                                ([['1','2'],['1'],['1','2']],False)):
            with tempfile.TemporaryDirectory() as tmp:
                with patch('member_batch.inspect_member_visual',side_effect=[report(s) for s in values]),\
                     patch('member_batch.inspect_member_search') as search:
                    r=inspect_initial_members({},Path(tmp)/'initial.png','1')
                self.assertEqual(r['initial_stable'],expected);search.assert_not_called()

    def test_initial_punctuation_and_avatar_zero_are_not_guessed_or_ignored(self):
        r=report(['2'])
        r['ocr']['words'].extend(visual_tests.VisualMemberTests().word(s,x,265) for s,x in [('，',620),('0',640)])
        r['analysis']=analyze_member_visual(r,'1')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_member_visual',return_value=r),\
                 patch('member_batch.inspect_member_search') as search:
                result=inspect_initial_members({},Path(tmp)/'initial.png','1')
            self.assertFalse(result['initial_stable']);search.assert_not_called()
            self.assertEqual(result['analysis']['selected_numbers'],['2','0'])
            self.assertEqual(result['analysis']['other_header_words'],['，'])

    def test_only_observed_empty_search_p_prepares_one_query_before_readonly_rechecks(self):
        r=report();r['ocr']['words']=[visual_tests.VisualMemberTests().word('p',620,265)]
        r['analysis']=analyze_member_visual(r,'1');events=[]
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_member_visual',side_effect=[r,report(),report()]),\
                 patch('member_batch.inspect_member_search',side_effect=lambda *args: events.append('search') or report()) as search:
                result=inspect_initial_members({},Path(tmp)/'initial.png','1',lambda:events.append('pending'))
            self.assertTrue(result['initial_stable']);self.assertTrue(result['initial_search_applied'])
            self.assertEqual(events,['pending','search']);search.assert_called_once()

    def test_changed_initial_dialog_stops_without_search(self):
        changed=report(['1','2']);changed['dialog_runtime_id']='changed'
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_member_visual',side_effect=[report(['1','2']),changed]) as reads,\
                 patch('member_batch.inspect_member_search') as search:
                with self.assertRaises(ValueError):inspect_initial_members({},Path(tmp)/'initial.png','1')
            self.assertEqual(reads.call_count,2);search.assert_not_called()

    def test_already_selected_pair_needs_two_final_matching_reads_without_clicking(self):
        for sets,expected in (([['1'],['1','2'],['1','2']],True),
                              ([['1','2'],['1','2'],['1']],False)):
            with tempfile.TemporaryDirectory() as tmp:
                with patch('member_batch.inspect_initial_members',return_value=initial_report(['1','2'])),\
                     patch('member_batch.inspect_member_visual',side_effect=[report(s) for s in sets]) as reads,\
                     patch('member_batch.select_member_test') as action:
                    result=select_members_test({},Path(tmp)/'batch.json',['1','2'])
                action.assert_not_called();self.assertEqual(reads.call_count,3)
                self.assertEqual(result['selection_verified'],expected)
                self.assertEqual(len(result['final_reads']),3)
                self.assertFalse(result['database_updated']);self.assertFalse(result['final_invite_clicked'])

    def test_initial_search_uses_prepare_and_requires_exact_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'initial.png'
            for readback in ({'number':'1','value_matches':True},
                             {'number':'2','value_matches':True},
                             {'number':'1','value_matches':False}, {}):
                r=report(target='1');r['search_preparation']=readback
                with patch('controls_probe.run_window_script',return_value=r) as run:
                    if readback=={'number':'1','value_matches':True}:
                        result=inspect_member_search({},path,'1')
                        self.assertEqual(result['analysis']['list_matches'],1)
                    else:
                        with self.assertRaises(ValueError):inspect_member_search({},path,'1')
                self.assertEqual(run.call_args.args[1],'select_member.ps1')
                self.assertEqual(run.call_args.args[2]['mode'],'prepare')
                self.assertEqual(run.call_args.args[2]['number'],'1')

    def test_initial_search_failure_stops_before_any_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_initial_members',side_effect=ValueError('读取失败')),\
                 patch('member_batch.select_member_test') as run,\
                 patch('member_batch.inspect_member_visual') as final:
                result=select_members_test({},Path(tmp)/'batch.json',['1','2'])
            run.assert_not_called();final.assert_not_called()
            self.assertFalse(result['initial_search_requested'])
            self.assertFalse(result['initial_search_applied'])
            self.assertFalse(result['final_invite_clicked'])
            self.assertEqual(result['steps'],[])

    def test_unknown_header_text_is_still_rejected_after_preparation(self):
        r=report();r['ocr']['words'].append(visual_tests.VisualMemberTests().word('p',620,265))
        r['analysis']=analyze_member_visual(r,'1')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_initial_members',return_value=initial_report_data(r)),\
                 patch('member_batch.select_member_test') as run:
                result=select_members_test({},Path(tmp)/'batch.json',['1','2'])
            run.assert_not_called();self.assertFalse(result['selection_verified'])
            self.assertEqual(r['analysis']['other_header_words'],['p'])

    def test_explicit_lists_ranges_and_legacy_names_are_preserved(self):
        self.assertEqual(parse_member_labels('1，2 3'),['1','2','3'])
        self.assertEqual(parse_member_labels('7-9'),['7','8','9'])
        self.assertEqual(parse_member_labels('001,002'),['001','002'])
        self.assertEqual(parse_member_labels('0-2'),['0','1','2'])
        for value in ('','1,1','1,001','5-2','1-41','001-003','1.0'):
            with self.assertRaises(ValueError,msg=value):parse_member_labels(value)

    def test_next_name_is_planned_without_toggling_the_previous_selection(self):
        before=report(['1'],'2');after=report(['1','2'])
        self.assertEqual(plan_member_selection(before,'2',['1'])['action'],'click_once')
        self.assertTrue(verify_member_selection(before,after,'2',['1'])['selection_verified'])
        changed=report(['3','2'])
        self.assertFalse(verify_member_selection(before,changed,'2',['1'])['selection_verified'])
        with self.assertRaises(ValueError):plan_member_selection(report(['3'],'2'),'2',['1'])

    def test_two_names_run_in_order_and_final_snapshot_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            def select(window,path,label,**kwargs):
                calls.append((label,list(kwargs['expected_selected'])))
                self.assertEqual(kwargs['expected_dialog']['dialog_runtime_id'],'42,100')
                return {'number':label,'selection_verified':True,'state':'selected'}
            with patch('member_batch.inspect_initial_members',return_value=initial_report()),\
                 patch('member_batch.inspect_member_visual',return_value=report(['1','2'])),\
                 patch('member_batch.select_member_test',side_effect=select):
                result=select_members_test({},Path(tmp)/'batch.json',['1','2'])
            self.assertEqual(calls,[('1',[]),('2',['1'])])
            self.assertTrue(result['selection_verified']);self.assertFalse(result['final_invite_clicked'])
            self.assertFalse(result['database_updated'])
            self.assertEqual(result['state'],'waiting_for_manual_invite')

    def test_existing_requested_selection_is_kept_and_not_clicked_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_initial_members',return_value=initial_report(['1'])),\
                 patch('member_batch.inspect_member_visual',return_value=report(['1','2'])),\
                 patch('member_batch.select_member_test',return_value={'selection_verified':True}) as run:
                result=select_members_test({},Path(tmp)/'batch.json',['1','2'])
            self.assertTrue(result['selection_verified']);run.assert_called_once()
            self.assertEqual(run.call_args.args[2],'2')
            self.assertEqual(run.call_args.kwargs['expected_selected'],('1',))

    def test_unexpected_preselected_name_stops_without_clicking_or_clearing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('member_batch.inspect_initial_members',return_value=initial_report(['9'])),\
                 patch('member_batch.select_member_test') as run:
                result=select_members_test({},Path(tmp)/'batch.json',['1','2'])
            run.assert_not_called();self.assertFalse(result['selection_verified'])

    def test_failed_member_stops_later_members_and_keeps_confirmed_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcomes=[{'selection_verified':True},{'selection_verified':False,'reason':'未找到'}]
            with patch('member_batch.inspect_initial_members',return_value=initial_report()) as inspect,\
                 patch('member_batch.select_member_test',side_effect=outcomes) as run:
                result=select_members_test({},Path(tmp)/'batch.json',['1','2','3'])
            self.assertEqual(run.call_count,2);inspect.assert_called_once()
            self.assertEqual(result['selected_numbers'],['1'])
            self.assertEqual(result['remaining_numbers'],['2','3'])
            self.assertFalse(result['selection_verified'])

    def test_changed_dialog_or_extra_final_selection_does_not_become_waiting(self):
        for kind in ('dialog','extra'):
            with tempfile.TemporaryDirectory() as tmp:
                final=report(['1','2','3'] if kind=='extra' else ['1','2'])
                if kind=='dialog':final['dialog_runtime_id']='changed'
                with patch('member_batch.inspect_initial_members',return_value=initial_report()),\
                     patch('member_batch.inspect_member_visual',return_value=final),\
                     patch('member_batch.select_member_test',return_value={'selection_verified':True}):
                    result=select_members_test({},Path(tmp)/'batch.json',['1','2'])
                self.assertEqual(result['state'],'review');self.assertFalse(result['selection_verified'])


if __name__=='__main__':unittest.main(verbosity=2)
