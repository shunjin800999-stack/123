import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from controls_probe import select_member_test, WindowActionError
from visual_members import plan_member_selection, verify_member_selection
import test_visual_members as visual_tests


def live_report(selected=False):
    helper=visual_tests.VisualMemberTests()
    report=helper.report()
    report.update(window_handle=100,process_id=200,dialog_runtime_id='42,100',final_invite_clicked=False,
        search_preparation={'number':'1','value_matches':True,'search_attempted':True},
        button_regions={'add':{'left':900,'top':745,'width':50,'height':30},
                        'cancel':{'left':810,'top':745,'width':70,'height':30}})
    report['ocr']['words']=[helper.word('1',645,265) if selected else helper.word('1',675,320)]
    return report


class MemberSelectionTests(unittest.TestCase):
    def test_changed_layout_is_recaptured_and_replanned_before_exactly_one_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'step.png';calls=[];prechecks=0
            def run(window,script,payload):
                nonlocal prechecks
                calls.append(payload.get('mode'))
                if payload.get('mode')=='prepare':
                    Path(payload['image_path']).write_bytes(b'original');return live_report()
                if payload.get('mode')=='check':
                    prechecks+=1
                    if prechecks==1:
                        fresh=live_report();fresh['regions']['list']['height']=66
                        raise WindowActionError('List layout changed',{'ok':False,'mode':'check',
                            'read_only':True,'click_attempted':False,'click_sent':False,
                            'search_attempted':False,'final_invite_clicked':False,'guard_stage':'layout','guard_report':fresh})
                    self.assertEqual(payload['regions']['list']['height'],66)
                    self.assertEqual((payload['x'],payload['y']),(690,325))
                    return {'ok':True,'mode':'check','read_only':True,'guard_stable':True,
                        'click_attempted':False,'click_sent':False,'final_invite_clicked':False,
                        'number':payload['number'],'before_sha256':payload['before_sha256']}
                if payload.get('mode')=='click_once':
                    self.assertEqual((payload['x'],payload['y']),(690,325))
                    self.assertIn('_before_refresh1.png',payload['before_image_path'])
                    return {'ok':True,'click_sent':True}
                if calls.count('click_once'):
                    return live_report(True)
                Path(payload['image_path']).write_bytes(b'new capture')
                fresh=live_report();fresh['regions']['list']['height']=66
                fresh['ocr']['words']=[visual_tests.VisualMemberTests().word('1',685,320)]
                return fresh
            with patch('controls_probe.run_window_script',side_effect=run),patch('controls_probe.time.sleep'):
                result=select_member_test({},path,'1',header_reader=lambda r:r)
            self.assertTrue(result['selection_verified']);self.assertEqual(calls.count('prepare'),1)
            self.assertEqual(calls.count('check'),2);self.assertEqual(calls.count('click_once'),1)
            self.assertEqual(len(result['preflight_reads']),2);self.assertFalse(result['final_invite_clicked'])

    def test_preflight_unsafe_or_unconfirmed_failures_never_refresh_or_click(self):
        for kind in ('attempted','sent','search','header','identity','unknown'):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'step.png';calls=[]
                def run(window,script,payload):
                    calls.append(payload.get('mode'))
                    if payload.get('mode')=='prepare':
                        Path(payload['image_path']).write_bytes(b'original');return live_report()
                    fresh=live_report()
                    guard={'ok':False,'mode':'check','read_only':True,'click_attempted':False,
                        'click_sent':False,'search_attempted':False,'final_invite_clicked':False,
                        'guard_stage':'layout','guard_report':fresh}
                    if kind=='attempted':guard['click_attempted']=True
                    elif kind=='sent':guard['click_sent']=True
                    elif kind=='search':guard['search_attempted']=True
                    elif kind=='header':guard['guard_stage']='header'
                    elif kind=='identity':fresh['dialog_runtime_id']='changed'
                    else:guard.pop('click_attempted')
                    raise WindowActionError('guard failed',guard)
                with patch('controls_probe.run_window_script',side_effect=run):
                    result=select_member_test({},path,'1',header_reader=lambda r:r)
                self.assertEqual(calls,['prepare','check'],kind)
                self.assertFalse(result['click_requested']);self.assertFalse(result['selection_verified'])

    def test_preflight_refresh_remains_bounded_with_later_duplicate_results(self):
        for kind in ('changing','duplicate'):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'step.png';calls=[]
                def run(window,script,payload):
                    calls.append(payload.get('mode'))
                    if payload.get('mode')=='prepare':
                        Path(payload['image_path']).write_bytes(b'original');return live_report()
                    if payload.get('mode')=='check':
                        raise WindowActionError('row changed',{'ok':False,'mode':'check','read_only':True,
                            'click_attempted':False,'click_sent':False,'search_attempted':False,
                            'final_invite_clicked':False,'guard_stage':'row','guard_report':live_report()})
                    Path(payload['image_path']).write_bytes(b'new capture')
                    fresh=live_report()
                    if kind=='duplicate':fresh['ocr']['words'].append(visual_tests.VisualMemberTests().word('1',675,370))
                    return fresh
                with patch('controls_probe.run_window_script',side_effect=run):
                    result=select_member_test({},path,'1',header_reader=lambda r:r)
                self.assertEqual(calls.count('prepare'),1);self.assertEqual(calls.count('click_once'),0)
                self.assertEqual(calls.count('check'),3)
                if kind=='duplicate':
                    self.assertEqual(result['plan']['analysis']['list_match_policy'],'first_matching_numeric_row')
                    self.assertEqual(result['plan']['analysis']['list_matches'],1)
                self.assertFalse(result['selection_verified'])

    def test_actual_click_guard_failure_is_not_retried_after_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'step.png';calls=[]
            def run(window,script,payload):
                calls.append(payload.get('mode'))
                if payload.get('mode')=='prepare':
                    Path(payload['image_path']).write_bytes(b'original');return live_report()
                if payload.get('mode')=='check':
                    return {'ok':True,'mode':'check','read_only':True,'guard_stable':True,
                        'click_attempted':False,'click_sent':False,'final_invite_clicked':False,
                        'number':payload['number'],'before_sha256':payload['before_sha256']}
                raise WindowActionError('layout changed',{'ok':False,'mode':'click_once','click_attempted':False,'click_sent':False})
            with patch('controls_probe.run_window_script',side_effect=run):
                result=select_member_test({},path,'1',header_reader=lambda r:r)
            self.assertEqual(calls,['prepare','check','click_once'])
            self.assertFalse(result['selection_verified']);self.assertFalse(result['click_sent'])

    def test_transient_ocr_requires_two_final_matching_reads_and_only_one_click(self):
        for observations,expected in (([False,True,True],True),([True,False,True],False),([True,True,False],False)):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'selection.png';calls=[];reads=iter(observations)
                def run(window,script,payload):
                    calls.append((script,payload.get('mode')))
                    if payload.get('mode')=='prepare':
                        Path(payload['image_path']).write_bytes(b'before');return live_report()
                    if payload.get('mode')=='click_once':return {'ok':True,'click_sent':True}
                    return live_report(next(reads))
                with patch('controls_probe.run_window_script',side_effect=run),patch('controls_probe.time.sleep'):
                    result=select_member_test({},path,'1')
                self.assertEqual(result['selection_verified'],expected)
                self.assertEqual(calls.count(('select_member.ps1','click_once')),1)
                self.assertEqual(len(result['verification_reads']),3)
                self.assertEqual([r['selection_verified'] for r in result['verification_reads']],observations)
                self.assertFalse(result['final_invite_clicked'])

    def test_changed_dialog_during_readonly_recheck_stops_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'selection.png';changed=live_report(True)
            changed['dialog_runtime_id']='changed'
            def run(window,script,payload):
                if payload.get('mode')=='prepare':
                    Path(payload['image_path']).write_bytes(b'before');return live_report()
                if payload.get('mode')=='click_once':return {'ok':True,'click_sent':True}
                return changed
            with patch('controls_probe.run_window_script',side_effect=run) as action,patch('controls_probe.time.sleep'):
                result=select_member_test({},path,'1')
            self.assertFalse(result['selection_verified']);self.assertEqual(action.call_count,3)
            self.assertEqual(len(result['verification_reads']),1)

    def test_unique_visible_name_plans_one_list_point(self):
        plan=plan_member_selection(live_report(),'1')
        self.assertEqual((plan['action'],plan['x'],plan['y']),('click_once',680,325))

    def test_already_selected_is_not_toggled(self):
        self.assertEqual(plan_member_selection(live_report(True),'1')['action'],'already_selected')

    def test_missing_duplicate_date_or_search_only_is_never_clicked(self):
        helper=visual_tests.VisualMemberTests()
        for kind in ('missing','duplicate','date','search'):
            report=live_report()
            if kind=='missing':report['ocr']['words']=[]
            elif kind=='duplicate':report['ocr']['words'].append(helper.word('1',675,370))
            elif kind=='date':report['ocr']['words']=[helper.word('last',645,320),helper.word('1',675,320)]
            else:report['ocr']['words']=[helper.word('1',710,265)]
            with self.assertRaises(ValueError,msg=kind):plan_member_selection(report,'1')

    def test_other_selected_member_missing_identity_or_final_button_overlap_stops(self):
        helper=visual_tests.VisualMemberTests()
        for kind in ('other_selected','other_header','identity','button','clipped'):
            report=live_report()
            if kind=='other_selected':report['ocr']['words'].append(helper.word('2',645,265))
            elif kind=='other_header':report['ocr']['words'].append(helper.word('Someone',645,265))
            elif kind=='identity':report.pop('dialog_runtime_id')
            elif kind=='button':report['button_regions']['add']={'left':670,'top':310,'width':30,'height':30}
            else:report['regions']['viewport']['left']=678
            with self.assertRaises(ValueError,msg=kind):plan_member_selection(report,'1')

    def test_verification_requires_same_dialog_and_new_exact_selected_chip(self):
        before=live_report();after=live_report(True)
        self.assertTrue(verify_member_selection(before,after,'1')['selection_verified'])
        for key,value in [('process_id',201),('window_handle',101),('dialog_runtime_id','changed'),
                          ('capture',{'left':601,'top':200,'width':360,'height':580}),
                          ('read_only',False),('final_invite_clicked',True)]:
            changed=copy.deepcopy(after);changed[key]=value
            self.assertFalse(verify_member_selection(before,changed,'1')['selection_verified'],key)
        self.assertFalse(verify_member_selection(before,live_report(),'1')['selection_verified'])

    def test_action_is_journaled_before_click_and_verified_without_inviting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'selection.png';calls=[]
            def run(window,script,payload):
                calls.append((script,payload.get('mode')))
                if payload.get('mode')=='prepare':
                    self.assertEqual(payload['number'],'1')
                    Path(payload['image_path']).write_bytes(b'fresh-before-image')
                    return live_report()
                if payload.get('mode')=='click_once':
                    saved=json.loads(path.with_suffix('.json').read_text())
                    self.assertTrue(saved['click_requested'])
                    self.assertFalse(saved['selection_verified'])
                    self.assertFalse(saved['final_invite_clicked'])
                    self.assertEqual(payload['dialog_runtime_id'],'42,100')
                    return {'ok':True,'click_sent':True,'final_invite_clicked':False}
                Path(payload['image_path']).write_bytes(b'fresh-after-image')
                return live_report(True)
            with patch('controls_probe.run_window_script',side_effect=run),patch('controls_probe.time.sleep'):
                result=select_member_test({},path,'1')
            self.assertEqual(calls,[('select_member.ps1','prepare'),('select_member.ps1','click_once'),('inspect_member_visual.ps1',None),('inspect_member_visual.ps1',None),('inspect_member_visual.ps1',None)])
            self.assertEqual(result['state'],'selected')
            self.assertTrue(result['selection_verified']);self.assertFalse(result['final_invite_clicked'])
            self.assertTrue(result['search_applied'])
            saved=json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(saved['scope'],'single_member_selection')
            self.assertIn('after',saved)

    def test_click_error_is_retained_and_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'selection.png';calls=[]
            def run(window,script,payload):
                calls.append(payload.get('mode'))
                if payload.get('mode')=='prepare':
                    Path(payload['image_path']).write_bytes(b'before');return live_report()
                raise WindowActionError('Input was partial',{'ok':False,'click_attempted':True,'click_sent':False})
            with patch('controls_probe.run_window_script',side_effect=run):result=select_member_test({},path,1)
            self.assertEqual(calls,['prepare','click_once'])
            self.assertEqual(result['state'],'review');self.assertFalse(result['selection_verified'])
            self.assertTrue(result['action_error']['click_attempted'])

    def test_already_selected_returns_without_click_and_old_after_image_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'selection.png';path.write_bytes(b'stale-after-image')
            with patch('controls_probe.run_window_script',return_value=live_report(True)) as run:
                result=select_member_test({},path,1)
            run.assert_called_once()
            self.assertFalse(path.exists());self.assertFalse(result['click_requested'])
            self.assertEqual(result['state'],'already_selected')

    def test_failed_postcheck_is_reviewed_without_second_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'selection.png';calls=[]
            def run(window,script,payload):
                calls.append(payload.get('mode'))
                if payload.get('mode')=='prepare':
                    Path(payload['image_path']).write_bytes(b'before');return live_report()
                if payload.get('mode')=='click_once':return {'ok':True,'click_sent':True}
                return live_report()
            with patch('controls_probe.run_window_script',side_effect=run),patch('controls_probe.time.sleep'):
                result=select_member_test({},path,1)
            self.assertEqual(calls,['prepare','click_once',None,None,None])
            self.assertTrue(result['click_sent']);self.assertFalse(result['selection_verified'])
            self.assertEqual(result['state'],'review')

    def test_unconfirmed_or_wrong_search_never_requests_a_selection_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            for wrong in ({},{'number':'2','value_matches':True},{'number':'1','value_matches':False}):
                report=live_report();report['search_preparation']=wrong
                path=Path(tmp)/'selection.png'
                with patch('controls_probe.run_window_script',return_value=report) as run:
                    result=select_member_test({},path,1)
                run.assert_called_once()
                self.assertFalse(result['click_requested']);self.assertFalse(result['selection_verified'])
                self.assertEqual(result['state'],'stopped_before_click')


if __name__=='__main__':unittest.main(verbosity=2)
