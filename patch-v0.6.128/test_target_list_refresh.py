"""Actual1000 OCR data across target-mode list refresh and success recording.

Windows actions and PNGs are simulated. A separate integration test recognizes
reproduced1000 pixels with the shipped, checksum-verified OCR models.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image,ImageDraw,ImageFont

from controls_probe import select_member_test,WindowActionError
from member_batch import select_members_test
from ocr_worker import infer_capture,runtime
from test_target_only_members import FONT,HAS_MODEL,WINDOW
from visual_members import analyze_member_visual,verify_target_member_selection
from window_queue import ready_for_manual_invite

FIXTURE=Path(__file__).parent/'test_fixtures'/'target-list-refresh-1000.json'


class TargetListRefreshTests(unittest.TestCase):
    def replay(self,*,failures=1,refresh_change=None,guard_change=None,initial_change=None,
               after_change=None,pause=False,real_model=False,number='1000',ledger=None):
        fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
        calls=[];payloads=[];sent=[];ocr=[];post_reads=[];event=threading.Event();guards=0
        def change_name(report):
            r=copy.deepcopy(report)
            r['header_scroll']['search_value']='' if r.get('header_scan_only') else number
            if 'search_preparation' in r:r['search_preparation']['number']=number
            for word in r['ocr']['words']:
                if word['text']=='1000':word['text']=number
            return r
        def write(report):
            image=Image.new('RGB',(728,1160),'white');draw=ImageDraw.Draw(image)
            font=ImageFont.truetype(str(FONT),20)
            for x,name in ((16,'999'),(174,number)):
                draw.rounded_rectangle((x,188,x+156,252),radius=32,fill=(241,241,241))
                draw.ellipse((x,188,x+64,252),fill=(252,170,63))
                draw.text((x+25,207),'1',font=font,fill='white')
                draw.text((x+75,207),name,font=font,fill=(30,30,30))
            # A fresh load is distinct evidence even when row coordinates stay put.
            draw.point((727,1159),fill=(guards,0,0))
            image.save(report['image_path'])
        def native(window,script,payload):
            nonlocal guards
            mode=payload.get('mode',script);calls.append(mode)
            if mode=='prepare':
                r=change_name(fixture['initial'])
                if initial_change:initial_change(r)
            elif mode=='click_once':
                guards+=1;payloads.append(copy.deepcopy(payload))
                if guards<=failures:
                    g=copy.deepcopy(fixture['action_error']);height=payload['regions']['list']['height']
                    new=244 if height==76 else height+56
                    g['guard_report']['regions']=copy.deepcopy(payload['regions'])
                    g['guard_report']['regions']['list']['height']=new
                    g['guard_report']['header_scroll']['search_value']=number
                    g['region_changes']=[{'region':'list','field':'height','before':height,'current':new}]
                    if guard_change:guard_change(g)
                    raise WindowActionError('List layout changed. No click.',g)
                sent.append(number)
                return {'ok':True,'click_attempted':True,'click_sent':True,'final_invite_clicked':False}
            elif mode=='inspect_member_visual.ps1':
                r=change_name(fixture['after' if sent else 'refresh'])
                if not sent:
                    r['regions']['list']['height']=244+max(0,guards-1)*56
                    # A read-only inspector does NOT return search_preparation.
                    self.assertNotIn('search_preparation',r)
                    if refresh_change:refresh_change(r)
                else:
                    post_reads.append(r)
                    if after_change:after_change(r)
            else:self.fail(mode)
            r['image_path']=payload['image_path'];write(r)
            return r
        def worker(report):
            stage=report['target_only_checkpoint']['stage'];ocr.append(stage)
            if real_model and stage=='after':
                engine,version,Image=runtime()
                return infer_capture(report,engine,version,Image)
            # Replay actual confidence, words and gray-label evidence. This
            # test targets the coordination, not OCR inference on original PNGs.
            b=copy.deepcopy(fixture['after' if stage=='after' else 'refresh']['backup_ocr'])
            b['image_sha256']=hashlib.sha256(Path(report['image_path']).read_bytes()).hexdigest()
            for word in b['accepted']+b['rejected']+b.get('detections',[])+b.get('header_crop_detections',[]):
                if word['text']=='1000':word['text']=number
            if stage=='after':b['selected_numbers']=[number]
            else:
                b['member_row_scan']['number']=number
                for scan in b['member_row_scan']['scans']:scan['analysis']['number']=number
            return b
        def wait(seconds):
            if pause and seconds==.3:event.set()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'step.png'
            with patch('controls_probe.run_window_script',side_effect=native),\
                 patch('backup_ocr.run_backup_ocr',side_effect=worker),\
                 patch('controls_probe.time.sleep',side_effect=wait) as waits:
                r=select_member_test(WINDOW,path,number,
                    expected_selected=fixture['expected_selected'] if ledger is None else ledger,
                    fast_visible=True,target_only=True,should_stop=event.is_set)
            self.assertEqual(r,json.loads(path.with_suffix('.json').read_text(encoding='utf-8')))
        return r,calls,payloads,sent,ocr,waits

    def test_actual1000_refresh_keeps_search_confirmation_and_finishes(self):
        r,calls,payloads,sent,ocr,waits=self.replay()
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(calls,['prepare','click_once','inspect_member_visual.ps1','click_once','inspect_member_visual.ps1'])
        self.assertEqual(sent,['1000']);self.assertEqual(ocr,['before','before','after'])
        self.assertEqual([p['regions']['list']['height'] for p in payloads],[76,244])
        search=r['before']['search_preparation']
        self.assertTrue(search['value_matches']);self.assertEqual(search['number'],'1000')
        self.assertFalse(search['search_attempted'])
        self.assertEqual(search['source'],'current_value_after_list_refresh')
        self.assertEqual(len(r['verification_reads']),1);self.assertNotIn('post_click_retry',r)
        self.assertEqual(r['after']['backup_ocr']['accepted'][0]['score'],.99998)
        self.assertEqual([a.args[0] for a in waits.call_args_list],[.3,.2])
        self.assertNotEqual(payloads[0]['before_sha256'],payloads[1]['before_sha256'])
        self.assertTrue(verify_target_member_selection(r['before'],r['after'],'1000',r['expected_selected'])['selection_verified'])

    def test_two_pre_input_refreshes_still_send_one_click(self):
        r,_,payloads,sent,ocr,_=self.replay(failures=2)
        self.assertTrue(r['selection_verified'],r['reason']);self.assertEqual(sent,['1000'])
        self.assertEqual([p['regions']['list']['height'] for p in payloads],[76,244,300])
        self.assertEqual(ocr,['before','before','before','after'])

    def test_refresh_query_changed_does_not_create_confirmation_or_click(self):
        r,calls,_,sent,ocr,_=self.replay(refresh_change=lambda r:r['header_scroll'].update(search_value='1001'))
        self.assertFalse(r['selection_verified']);self.assertEqual(sent,[])
        self.assertEqual(calls,['prepare','click_once','inspect_member_visual.ps1']);self.assertEqual(ocr,['before'])
        self.assertIn('搜索内容已改变',r['reason'])

    def test_refresh_window_changed_stops_before_replan(self):
        r,calls,_,sent,ocr,_=self.replay(refresh_change=lambda r:r.update(process_id=201))
        self.assertFalse(r['selection_verified']);self.assertEqual(sent,[]);self.assertEqual(ocr,['before'])
        self.assertIn('窗口或弹窗改变',r['reason'])

    def test_uncertain_input_does_not_allow_another_guard_or_click(self):
        r,calls,_,sent,_,_=self.replay(guard_change=lambda g:g.update(click_attempted=True))
        self.assertEqual(calls,['prepare','click_once']);self.assertEqual(sent,[])
        self.assertFalse(r['selection_verified']);self.assertNotIn('list_layout_refreshes',r)

    def test_repeated_loading_stops_after_existing_bound(self):
        r,calls,payloads,sent,ocr,_=self.replay(failures=10)
        self.assertFalse(r['selection_verified']);self.assertEqual(sent,[])
        self.assertEqual(len(payloads),3);self.assertEqual(len(r['list_layout_refreshes']),3)
        self.assertEqual(ocr,['before','before','before'])

    def test_initial_query_not_confirmed_is_not_repaired_by_refresh(self):
        r,calls,_,sent,ocr,_=self.replay(initial_change=lambda r:r['search_preparation'].update(value_matches=False))
        self.assertEqual(calls,['prepare']);self.assertEqual(sent,[]);self.assertEqual(ocr,[])
        self.assertFalse(r['selection_verified'])

    def test_pause_during_existing_loading_wait_sends_no_click(self):
        r,calls,_,sent,_,_=self.replay(pause=True)
        self.assertEqual(r['state'],'paused');self.assertEqual(sent,[])
        self.assertEqual(calls,['prepare','click_once'])

    def test_target_proof_from_different_window_still_fails(self):
        r,calls,_,sent,_,_=self.replay(after_change=lambda r:r.update(process_id=201))
        self.assertFalse(r['selection_verified']);self.assertEqual(sent,['1000'])
        self.assertEqual(calls.count('inspect_member_visual.ps1'),2)
        self.assertNotIn('post_click_retry',r)

    def test_successful1000_allows_batch_to_search1001_without_final_reread(self):
        first=self.replay(ledger=[])[0];second=self.replay(number='1001',failures=0,ledger=['1000'])[0]
        # Build a bound empty initial-page proof for the batch coordinator.
        initial=copy.deepcopy(first['before']);initial.pop('target_only_checkpoint')
        initial['visible_header_checkpoint']={'scope':'visible_pair','ledger':[],'number':'1000','stage':'before'}
        initial['backup_ocr']['target_member_only']=False
        initial['analysis']=analyze_member_visual(initial,'1000')
        calls=[]
        def select(window,path,number,**kwargs):
            calls.append(number)
            return copy.deepcopy(first if number=='1000' else second)
        with tempfile.TemporaryDirectory() as folder,\
             patch('member_batch.inspect_member_search',return_value=initial),\
             patch('backup_ocr.apply_visible_header',return_value=initial),\
             patch('member_batch.select_member_test',side_effect=select),\
             patch('member_batch.inspect_final_member') as final:
            r=select_members_test(WINDOW,Path(folder)/'batch.json',['1000','1001'],fast_visible=True,target_only=True)
        self.assertTrue(r['selection_verified'],r['reason']);self.assertEqual(calls,['1000','1001'])
        self.assertEqual(r['selected_numbers'],['1000','1001']);self.assertEqual(r['final_reads'],[])
        self.assertTrue(ready_for_manual_invite(r,WINDOW,['1000','1001']));final.assert_not_called()

    @unittest.skipUnless(HAS_MODEL,'Requires shipped OCR models')
    def test_real_model1000_post_read_passes_after_pre_input_refresh(self):
        r,calls,_,sent,_,_=self.replay(real_model=True)
        self.assertTrue(r['selection_verified'],r['reason']);self.assertEqual(sent,['1000'])
        self.assertEqual(len(r['verification_reads']),1)
        self.assertEqual(r['after']['backup_ocr']['selected_numbers'],['1000'])


if __name__=='__main__':unittest.main()
