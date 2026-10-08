"""Visible checkpoints may be partial; only the final full read permits Add."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from controls_probe import select_member_test
from member_batch import select_members_test
from visual_members import analyze_member_visual, verify_visible_member_selection
from test_selection_header_scan import page, fixture, WINDOW


def checkpoint(report,ledger,number,stage):
    report=copy.deepcopy(report)
    report.pop('full_header_scan',None)
    report['visible_header_checkpoint']={'scope':'visible_pair','ledger':list(ledger),'number':number,'stage':stage}
    return report


def pair():
    ledger=[str(n) for n in range(24,32)]
    before=page(20,tuple(ledger),124)
    before['ocr']['words']=[{'text':'32','left':80,'top':324,'width':40,'height':32}]
    before['search_preparation']={'number':'32','value_matches':True}
    after=page(60,tuple(ledger+['32']),164)
    return checkpoint(before,ledger,'32','before'),checkpoint(after,ledger,'32','after'),ledger


class FastMemberTests(unittest.TestCase):
    def test_wrap_can_hide_older_chips_without_mid_step_scroll(self):
        a,b,ledger=pair()
        self.assertNotIn('31',b['backup_ocr']['selected_numbers'])
        self.assertTrue(verify_visible_member_selection(a,b,'32',ledger)['selection_verified'])
        self.assertFalse(verify_visible_member_selection(a,b,'32',ledger)['final_invite_clicked'])

    def test_wrong_previous_duplicate_unknown_target_or_window_never_passes(self):
        for kind in ('previous','duplicate','unknown','missing_target','window','stage','offset'):
            a,b,ledger=pair()
            if kind=='previous':a['visible_header_checkpoint']['ledger'][-1]='90'
            if kind in ('duplicate','unknown','missing_target'):
                words=b['backup_ocr']['accepted']
                if kind=='duplicate':words.append(copy.deepcopy(words[-1]))
                if kind=='unknown':words[-1]['text']='99'
                if kind=='missing_target':words.clear()
                b['backup_ocr']['selected_numbers']=[w['text'] for w in words]
            if kind=='window':b['process_id']=999
            if kind=='stage':b['visible_header_checkpoint']['stage']='before'
            if kind=='offset':b['header_scroll']['offset']=0
            self.assertFalse(verify_visible_member_selection(a,b,'32',ledger)['selection_verified'],kind)

    def test_empty_start_does_not_allow_existing_selection_or_hidden_chips(self):
        for r in (page(0,('24',),104),page(20,(),124)):
            r=checkpoint(r,[],'24','before')
            self.assertFalse(analyze_member_visual(r,'24')['usable'])

    def test_short_empty_search_header_can_start_without_scroll_geometry(self):
        from selection_header_scan import page_area,geometry
        from backup_ocr import FRAME_KEYS
        r=page(0,(),104)
        for rect in (r['header_scroll']['viewport'],r['header_scroll']['inner'],r['regions']['header']):rect['height']=44
        r['regions']['search']['top']=279
        r['regions']['header_ocr']=page_area(r,minimum_height=24)
        r['selection_header_ocr']['frame']={k:copy.deepcopy(r[k]) for k in FRAME_KEYS}
        self.assertTrue(analyze_member_visual(checkpoint(r,[],'24','before'),'24')['usable'])
        # Wheel scanning keeps its larger viewport requirement.
        with self.assertRaises(ValueError):geometry(r)

    def test_fast_step_sends_one_click_and_one_post_read_with_no_full_reader(self):
        a,b,ledger=pair();calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'step.png'
            def run(window,script,payload):
                mode=payload.get('mode');calls.append(mode or script)
                if mode=='prepare':
                    r=copy.deepcopy(a);r.pop('visible_header_checkpoint');r['image_path']=payload['image_path']
                    Path(r['image_path']).write_bytes(b'current frame');return r
                if mode=='check':return {'mode':'check','read_only':True,'guard_stable':True,
                    'click_attempted':False,'click_sent':False,'final_invite_clicked':False,
                    'number':'32','before_sha256':payload['before_sha256']}
                if mode=='click_once':return {'ok':True,'click_sent':True}
                r=copy.deepcopy(b);r.pop('visible_header_checkpoint');r['image_path']=payload['image_path'];return r
            with patch('controls_probe.run_window_script',side_effect=run),\
                 patch('backup_ocr.apply_visible_header',side_effect=checkpoint),patch('controls_probe.time.sleep'):
                result=select_member_test(WINDOW,path,'32',expected_selected=ledger,fast_visible=True,
                    header_reader=lambda _:self.fail('Full reader called during selection'))
            self.assertTrue(result['selection_verified'],result['reason'])
            self.assertEqual(calls,['prepare','click_once','inspect_member_visual.ps1'])
            self.assertEqual(len(result['verification_reads']),1)

    def test_fast_native_guard_failure_never_retries_click(self):
        from controls_probe import WindowActionError
        a,_,ledger=pair();calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            def run(window,script,payload):
                calls.append(payload['mode'])
                if payload['mode']=='prepare':
                    r=copy.deepcopy(a);r['image_path']=payload['image_path']
                    Path(r['image_path']).write_bytes(b'frame');return r
                self.assertIn('selected_name_bounds',payload)
                self.assertIn('before_sha256',payload)
                raise WindowActionError('row changed',{'ok':False,'mode':'click_once',
                    'guard_stage':'row','click_attempted':False,'click_sent':False,'final_invite_clicked':False})
            with patch('controls_probe.run_window_script',side_effect=run),patch('backup_ocr.apply_visible_header',side_effect=checkpoint):
                r=select_member_test(WINDOW,Path(tmp)/'step.png','32',expected_selected=ledger,fast_visible=True)
            self.assertEqual(calls,['prepare','click_once']);self.assertEqual(r['state'],'review')
            self.assertFalse(r['selection_verified']);self.assertFalse(r['click_sent'])
            self.assertFalse(r['final_invite_clicked'])

    def test_pause_before_step_never_searches_or_clicks(self):
        with tempfile.TemporaryDirectory() as tmp,patch('controls_probe.run_window_script') as native:
            result=select_member_test(WINDOW,Path(tmp)/'step.png','24',fast_visible=True,should_stop=lambda:True)
            native.assert_not_called();self.assertEqual(result['state'],'paused')
            self.assertFalse(result['click_requested']);self.assertFalse(result['final_invite_clicked'])

    def test_pause_after_recognition_prevents_physical_click(self):
        a,_,ledger=pair();event=threading.Event();calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            def run(window,script,payload):
                calls.append(payload['mode'])
                if payload['mode']=='prepare':
                    r=copy.deepcopy(a);r['image_path']=payload['image_path'];Path(r['image_path']).write_bytes(b'frame');return r
                event.set()
                return {'mode':'check','read_only':True,'guard_stable':True,'click_attempted':False,
                    'click_sent':False,'final_invite_clicked':False,'number':'32','before_sha256':payload['before_sha256']}
            def read(r,ledger,number,stage):
                result=checkpoint(r,ledger,number,stage);event.set();return result
            with patch('controls_probe.run_window_script',side_effect=run),patch('backup_ocr.apply_visible_header',side_effect=read):
                r=select_member_test(WINDOW,Path(tmp)/'step.png','32',expected_selected=ledger,
                    fast_visible=True,should_stop=event.is_set)
            self.assertEqual(r['state'],'paused');self.assertEqual(calls,['prepare']);self.assertFalse(r['click_sent'])

    def test_fast_batch_reads_complete_list_only_once_at_end(self):
        labels=[str(n) for n in range(24,44)];final=checkpoint(page(20,tuple(str(n) for n in range(38,44)),124),labels[:-1],'43','after');final['analysis']=analyze_member_visual(final,'43');steps=[]
        with tempfile.TemporaryDirectory() as tmp:
            def select(window,path,number,**kwargs):
                self.assertTrue(kwargs['fast_visible']);steps.append(number)
                return {'selection_verified':True,'number':number,'state':'selected'}
            raw=page(0,(),104)
            with patch('member_batch.inspect_member_search',return_value=raw),\
                 patch('backup_ocr.apply_visible_header',side_effect=checkpoint),\
                 patch('member_batch.select_member_test',side_effect=select),\
                 patch('member_batch.inspect_final_member',return_value=final) as complete,\
                 patch('member_batch.time.sleep'):
                r=select_members_test(WINDOW,Path(tmp)/'batch.json',labels,fast_visible=True)
            self.assertTrue(r['selection_verified'],r['reason']);self.assertEqual(steps,labels)
            self.assertEqual(complete.call_count,2);self.assertEqual(len(r['final_reads']),2)
            self.assertFalse(r['database_updated']);self.assertFalse(r['final_invite_clicked'])

    def test_final_last_member_uses_current_capture_without_scroll(self):
        from member_batch import inspect_final_member
        labels=[str(n) for n in range(24,44)]
        raw=page(20,tuple(str(n) for n in range(38,44)),124)
        with patch('controls_probe.inspect_member_visual',return_value=raw) as capture, \
             patch('backup_ocr.apply_visible_header',side_effect=checkpoint), \
             patch('selection_header_scan.read_current_header') as scroll:
            final=inspect_final_member(WINDOW,'final.png',labels)
        capture.assert_called_once_with(WINDOW,'final.png','43',first_row_scan=True,header_scan_only=True)
        scroll.assert_not_called()
        self.assertTrue(final['analysis']['usable'])
        self.assertEqual(final['analysis']['chip_matches'],1)
        self.assertNotIn('full_header_scan',final)

    def test_pause_after_first_verified_member_prevents_next_member_and_final_scan(self):
        event=threading.Event();calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            def select(window,path,number,**kwargs):
                calls.append(number);event.set()
                return {'selection_verified':True,'number':number,'state':'selected'}
            with patch('member_batch.inspect_member_search',return_value=page(0,(),104)),\
                 patch('backup_ocr.apply_visible_header',side_effect=checkpoint),\
                 patch('member_batch.select_member_test',side_effect=select),\
                 patch('member_batch.inspect_final_member') as complete:
                r=select_members_test(WINDOW,Path(tmp)/'batch.json',['24','25'],fast_visible=True,should_stop=event.is_set)
            self.assertEqual(r['state'],'paused');self.assertEqual(calls,['24']);complete.assert_not_called()
            self.assertEqual(r['selected_numbers'],['24']);self.assertEqual(r['remaining_numbers'],['25'])

    def test_partial_final_or_missing_number_cannot_become_invite_ready(self):
        for final in (checkpoint(page(20,tuple(str(n) for n in range(24,32)),124),
                                 [str(n) for n in range(24,31)],'31','after'),fixture(tuple(str(n) for n in range(24,43)))):
            final['analysis']=analyze_member_visual(final,'24')
            with tempfile.TemporaryDirectory() as tmp,\
                 patch('member_batch.inspect_member_search',return_value=page(0,(),104)),\
                 patch('backup_ocr.apply_visible_header',side_effect=checkpoint),\
                 patch('member_batch.select_member_test',return_value={'selection_verified':True}),\
                 patch('member_batch.inspect_final_member',return_value=final),patch('member_batch.time.sleep'):
                r=select_members_test(WINDOW,Path(tmp)/'batch.json',[str(n) for n in range(24,44)],fast_visible=True)
            self.assertFalse(r['selection_verified']);self.assertFalse(r['final_invite_clicked'])
