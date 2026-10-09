"""Latest 735 report: a new header capture after both recognizers miss text.

This runs the real selection and OCR guards with simulated PNGs/native replies
and model outputs; the uploaded report does not include its original PNG.
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

import numpy as np
from PIL import Image,ImageDraw

from backup_ocr import apply_visible_header
from controls_probe import select_member_test
from ocr_worker import infer_capture
from visual_members import verify_visible_member_selection


FIXTURE=Path(__file__).parent/'test_fixtures'/'post-click-735-empty.json'
WINDOW={'hwnd':100,'pid':200,'path':r'C:\test\Telegram.exe'}
CHIP=[[88,127],[138,127],[138,154],[88,154]]
NATIVE_WORD={'text':'735','left':92,'top':131,'width':42,'height':19}


class PostClickRecaptureTests(unittest.TestCase):
    def replay(self,*,first=(),second=('735',),scores=(1.,1.),native_second=False,
               local_text='735',worker_error=False,change_image=False,
               mutate_first=None,mutate_second=None,capture_error=False,pause=False):
        fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
        native_calls=[];ocr_calls=[];post_frames=[];clicks=[];event=threading.Event()
        def write(frame,post):
            image=Image.new('RGB',(728,1160),'white');draw=ImageDraw.Draw(image)
            if post and ((first if post==1 else second) or (post==2 and native_second)):
                draw.rectangle((80,120,151,163),fill=(241,241,241))
                for x in (93,108,123):draw.rectangle((x,133,x+6,150),fill=(40,40,40))
            image.save(frame['image_path'])
        def native(window,script,payload):
            mode=payload.get('mode',script);native_calls.append(mode)
            if mode=='click_once':
                clicks.append((payload['number'],payload['x'],payload['y']))
                return {'ok':True,'click_sent':True}
            post=len(post_frames)+1 if mode=='inspect_member_visual.ps1' else 0
            if post==2 and capture_error:raise RuntimeError('capture unavailable')
            frame=copy.deepcopy(fixture['after' if post else 'before'])
            frame['image_path']=payload['image_path']
            if post:
                self.assertEqual(set(payload),{'image_path','first_row_scan','header_scan_only'})
                self.assertTrue(payload['header_scan_only']);self.assertTrue(payload['first_row_scan'])
                if post==2 and native_second:frame['ocr']['words'].append(copy.deepcopy(NATIVE_WORD))
                mutate=mutate_first if post==1 else mutate_second
                if mutate:mutate(frame)
            write(frame,post)
            if post:post_frames.append(frame)
            return frame
        def worker(request):
            post=len(post_frames)
            if post==1 and worker_error:
                return {'ok':False,'error':'OCR unavailable','final_invite_clicked':False}
            def engine(crop,**flags):
                ocr_calls.append({'post':post,'shape':crop.shape,'flags':flags})
                if post:
                    if flags['use_det']:
                        self.assertEqual(crop.shape,(88,728,3))
                        labels=first if post==1 else second
                        result=SimpleNamespace(txts=list(labels),scores=[scores[post-1]]*len(labels),
                            boxes=[np.array([[x,y-96] for x,y in CHIP]) for _ in labels])
                    else:
                        self.assertEqual(post,2);self.assertEqual(crop.shape,(27,50,3))
                        result=SimpleNamespace(txts=[local_text],scores=[1.],boxes=None)
                    if post==1 and change_image:Path(request['image_path']).write_bytes(b'changed image')
                    return result
                self.assertEqual(crop.shape,(336,728,3))
                return SimpleNamespace(txts=['735','last seen recently'],scores=[1.,1.],boxes=[
                    np.array([[142,225],[194,225],[194,255],[142,255]]),
                    np.array([[142,264],[356,264],[356,301],[142,301]])])
            try:return infer_capture(request,engine,lambda _:'test',Image)
            except Exception as error:return {'ok':False,'error':str(error),'final_invite_clicked':False}
            finally:
                if post==1 and pause:event.set()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'step.png'
            with patch('controls_probe.run_window_script',side_effect=native),\
                 patch('backup_ocr.run_backup_ocr',side_effect=worker),\
                 patch('ocr_worker.recognize_header_ink',return_value=([],[])),\
                 patch('controls_probe.time.sleep') as wait:
                result=select_member_test(WINDOW,path,'735',expected_selected=[],fast_visible=True,
                    should_stop=event.is_set)
            self.assertEqual(result,json.loads(path.with_suffix('.json').read_text()))
            self.assertEqual(clicks,[('735',832,338)])
            self.assertEqual(native_calls,['prepare','click_once']+['inspect_member_visual.ps1']*(
                len(post_frames)+(1 if capture_error else 0)))
            wait.assert_called_once_with(.2)
            self.assertFalse(result['final_invite_clicked'])
            if len(post_frames)==2:
                images=[Path(f['image_path']) for f in post_frames]
                self.assertNotEqual(images[0],images[1]);self.assertTrue(all(p.exists() for p in images))
                for read,image in zip(result['verification_reads'],images):
                    backup=read['report']['backup_ocr']
                    if backup.get('ok'):
                        self.assertEqual(backup['image_sha256'],hashlib.sha256(image.read_bytes()).hexdigest())
        return result,native_calls,ocr_calls

    def test_report_735_both_sources_missing_recaptures_once_then_continues(self):
        result,native,ocr=self.replay()
        self.assertTrue(result['selection_verified'],result['reason'])
        self.assertEqual(len(result['verification_reads']),2)
        first,second=[read['report'] for read in result['verification_reads']]
        self.assertEqual(first['backup_ocr']['selected_numbers'],[])
        self.assertEqual(first['backup_ocr']['new_member_retry']['error'],
            '新增备注的Windows顶部数字位置不唯一或不可见')
        # Fresh RapidOCR proof is sufficient even when native OCR still misses.
        self.assertEqual([w for w in second['ocr']['words'] if w['text']=='735'],[])
        self.assertEqual(second['backup_ocr']['selected_numbers'],['735'])
        self.assertNotEqual(first['backup_ocr']['image_sha256'],second['backup_ocr']['image_sha256'])
        self.assertTrue(result['post_click_retry']['verified'])
        self.assertTrue(result['post_click_retry']['capture_requested'])
        self.assertEqual(result['post_click_retry']['max_retries'],1)
        self.assertEqual([c['post'] for c in ocr],[0,1,2])
        self.assertTrue(verify_visible_member_selection(result['before'],result['after'],'735',[])['selection_verified'])

    def test_first_success_does_not_recapture_or_recognize_again(self):
        result,native,ocr=self.replay(first=('735',))
        self.assertTrue(result['selection_verified'])
        self.assertEqual(len(result['verification_reads']),1)
        self.assertNotIn('post_click_retry',result)
        self.assertEqual([c['post'] for c in ocr],[0,1])

    def test_second_capture_still_empty_stops_without_third_capture(self):
        result,native,ocr=self.replay(second=())
        self.assertFalse(result['selection_verified']);self.assertEqual(result['state'],'review')
        self.assertEqual(len(result['verification_reads']),2)
        self.assertFalse(result['post_click_retry']['verified'])
        self.assertIn('重新截取顶部核验一次',result['reason'])
        self.assertEqual([c['post'] for c in ocr],[0,1,2])

    def test_wrong_duplicate_or_low_score_second_read_never_passes(self):
        for second,score in [(('736',),1.),(('735','735'),1.),(('735',),.79)]:
            with self.subTest(second=second,score=score):
                result,native,ocr=self.replay(second=second,scores=(1.,score))
                self.assertFalse(result['selection_verified']);self.assertEqual(len(result['verification_reads']),2)
                self.assertFalse(result['post_click_retry']['verified'])

    def test_80_percent_second_read_can_pass(self):
        result,native,ocr=self.replay(scores=(1.,.80))
        self.assertTrue(result['selection_verified']);self.assertEqual(len(result['verification_reads']),2)

    def test_new_frame_can_use_existing_native_local_crop_recovery(self):
        result,native,ocr=self.replay(second=(),native_second=True)
        self.assertTrue(result['selection_verified'])
        self.assertTrue(result['after']['backup_ocr']['new_member_retry']['verified'])
        self.assertEqual([c['post'] for c in ocr],[0,1,2,2])
        self.assertFalse(ocr[-1]['flags']['use_det'])

    def test_unknown_duplicate_or_low_score_first_evidence_does_not_recapture(self):
        for first,score in [(('736',),1.),(('735','735'),1.),(('735',),.79)]:
            with self.subTest(first=first,score=score):
                result,native,ocr=self.replay(first=first,scores=(score,1.))
                self.assertFalse(result['selection_verified']);self.assertEqual(len(result['verification_reads']),1)
                self.assertNotIn('post_click_retry',result)

    def test_corrupt_ocr_or_changed_image_cannot_trigger_new_capture(self):
        for kwargs in ({'worker_error':True},{'change_image':True}):
            with self.subTest(kwargs=kwargs):
                result,native,ocr=self.replay(**kwargs)
                self.assertFalse(result['selection_verified']);self.assertEqual(len(result['verification_reads']),1)
                self.assertNotIn('post_click_retry',result)

    def test_first_capture_identity_change_stops_before_recapture(self):
        for key,value in [('window_handle',999),('process_id',999),('dialog_runtime_id','other')]:
            with self.subTest(key=key):
                result,native,ocr=self.replay(mutate_first=lambda r:r.update({key:value}))
                self.assertFalse(result['selection_verified']);self.assertEqual(len(result['verification_reads']),1)
                self.assertNotIn('post_click_retry',result)

    def test_second_capture_identity_change_cannot_pass(self):
        for key,value in [('window_handle',999),('process_id',999),('dialog_runtime_id','other'),
                          ('capture',{'left':749,'top':218,'width':364,'height':580})]:
            with self.subTest(key=key):
                result,native,ocr=self.replay(mutate_second=lambda r:r.update({key:value}))
                self.assertFalse(result['selection_verified']);self.assertEqual(len(result['verification_reads']),2)
                self.assertIn('窗口或弹窗改变',result['reason'])

    def test_pause_after_failed_first_read_preserves_it_and_never_recaptures(self):
        result,native,ocr=self.replay(pause=True)
        self.assertFalse(result['selection_verified']);self.assertEqual(result['state'],'paused')
        self.assertEqual(len(result['verification_reads']),1)
        self.assertFalse(result['post_click_retry']['verified'])
        self.assertNotIn('capture_requested',result['post_click_retry'])

    def test_capture_failure_preserves_first_evidence_and_does_not_repeat_input(self):
        result,native,ocr=self.replay(capture_error=True)
        self.assertFalse(result['selection_verified']);self.assertEqual(len(result['verification_reads']),1)
        self.assertTrue(result['post_click_retry']['capture_requested'])
        self.assertFalse(result['post_click_retry']['verified'])
        self.assertIn('capture unavailable',result['reason'])


if __name__=='__main__':unittest.main()
