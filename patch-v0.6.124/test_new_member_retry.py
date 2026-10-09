"""735 report replay: native bounds rescue one missed newly selected chip.

The uploaded report has no PNG. Pixels and recognizer responses below are
simulated, using its actual native word bounds and dialog geometry.
"""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image,ImageDraw

from backup_ocr import apply_visible_header
from controls_probe import select_member_test
from ocr_worker import infer_capture
from visual_members import verify_visible_member_selection


FIXTURE=Path(__file__).parent/'test_fixtures'/'new-member-735.json'
WINDOW={'hwnd':100,'pid':200,'path':r'C:\test\Telegram.exe'}
CHIP_BOX=[[88,127],[138,127],[138,154],[88,154]]


class NewMemberRetryTests(unittest.TestCase):
    def scene(self,*,first=None,texts=('735',),scores=(1.,),native=None,
              background=(241,241,241),engine_error=None,change_image=False):
        fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
        report=fixture['after']
        if native is not None:report['ocr']['words']=native
        first=fixture['missed_header_detections'] if first is None else first
        calls=[]
        def write(path):
            image=Image.new('RGB',(728,1160),'white');draw=ImageDraw.Draw(image)
            draw.rectangle((80,120,151,163),fill=background)
            for x in (93,108,123):draw.rectangle((x,133,x+6,150),fill=(40,40,40))
            # Colored avatar and list marker must never enter the local crop.
            draw.rectangle((15,111,79,173),fill=(20,100,200))
            draw.rectangle((25,237,40,260),fill=(200,20,20))
            image.save(path)
        def engine(crop,**flags):
            calls.append({'shape':crop.shape,'flags':flags,'pixels':crop.copy()})
            if flags['use_det']:
                self.assertEqual(crop.shape,(88,728,3))
                return SimpleNamespace(txts=[d['text'] for d in first],
                    scores=[d['score'] for d in first],
                    boxes=[np.array([[x,y-96] for x,y in d['box']]) for d in first])
            self.assertEqual(flags,{'use_det':False,'use_cls':False,'use_rec':True})
            self.assertEqual(crop.shape,(27,50,3))
            if engine_error is not None:raise ValueError(engine_error)
            if change_image:Path(report['image_path']).write_bytes(b'changed frame')
            return SimpleNamespace(txts=texts,scores=scores,boxes=None)
        return report,calls,engine,write

    def recognize(self,*,stage='after',ledger=(),ink=None,mutate=None,**kwargs):
        report,calls,engine,write=self.scene(**kwargs)
        if mutate:mutate(report)
        with tempfile.TemporaryDirectory() as folder:
            report['image_path']=str(Path(folder)/'frame.png');write(report['image_path'])
            def worker(request):
                try:return infer_capture(request,engine,lambda _:'test',Image)
                except Exception as error:return {'ok':False,'error':str(error),'final_invite_clicked':False}
            # Before checkpoints normally use the separate progressive reader.
            if stage=='before':report['first_row_scan']=False
            with patch('backup_ocr.run_backup_ocr',side_effect=worker),\
                 patch('ocr_worker.recognize_header_ink',return_value=ink or ([],[])):
                result=apply_visible_header(report,ledger,'735',stage)
        return result,calls

    def test_735_native_crop_recovers_the_missed_new_chip_once(self):
        result,calls=self.recognize()
        self.assertTrue(result['selection_header_ocr']['ok'],result['selection_header_ocr'])
        retry=result['backup_ocr']['new_member_retry']
        self.assertTrue(retry['verified']);self.assertEqual(retry['max_retries'],1)
        self.assertEqual(retry['first']['selected_numbers'],[])
        self.assertEqual(retry['crop_bounds'],[88,127,138,154])
        self.assertEqual(retry['native_word']['text'],'735')
        self.assertEqual(result['backup_ocr']['selected_numbers'],['735'])
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False])
        self.assertFalse(np.any(np.all(calls[1]['pixels']==(200,100,20),axis=2)))
        self.assertFalse(np.any(np.all(calls[1]['pixels']==(20,20,200),axis=2)))

    def test_first_success_has_one_recognition_and_no_local_retry(self):
        result,calls=self.recognize(first=[{'text':'735','score':1.,'box':CHIP_BOX}])
        self.assertTrue(result['selection_header_ocr']['ok'])
        self.assertEqual(len(calls),1);self.assertTrue(calls[0]['flags']['use_det'])
        self.assertNotIn('new_member_retry',result['backup_ocr'])

    def test_existing_ink_recognition_success_never_adds_native_crop(self):
        result,calls=self.recognize(ink=([('735',1.,CHIP_BOX)],
            [{'text':'735','score':1.,'box':CHIP_BOX,'source':'gray_label_ink_crop'}]))
        self.assertTrue(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),1)
        self.assertNotIn('new_member_retry',result['backup_ocr'])

    def test_local_confidence_boundary_accepts_80_percent(self):
        result,calls=self.recognize(scores=(.80,))
        self.assertTrue(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),2)

    def test_bad_local_reads_stop_with_both_evidence_records_and_no_third_call(self):
        for texts,scores in [(('735',),(.79,)),(('736',),(1.,)),((),()),
                             (('735','735'),(1.,1.)),(('735',),())]:
            with self.subTest(texts=texts,scores=scores):
                result,calls=self.recognize(texts=texts,scores=scores)
                self.assertFalse(result['selection_header_ocr']['ok'])
                retry=result['backup_ocr']['new_member_retry']
                self.assertFalse(retry['verified']);self.assertIn('second',retry)
                self.assertEqual(len(calls),2)

    def test_wrong_unknown_duplicate_or_low_score_initial_evidence_cannot_retry(self):
        cases=([{'text':'736','score':1.,'box':CHIP_BOX}],
               [{'text':'735','score':.79,'box':CHIP_BOX}],
               [{'text':'735','score':1.,'box':CHIP_BOX}]*2,
               [{'text':'name','score':1.,'box':CHIP_BOX}])
        for first in cases:
            with self.subTest(first=first):
                result,calls=self.recognize(first=first)
                self.assertFalse(result['selection_header_ocr']['ok'])
                self.assertEqual(len(calls),1)
                self.assertNotIn('new_member_retry',result['backup_ocr'])

    def test_native_word_in_search_list_avatar_or_duplicate_cannot_supply_proof(self):
        native={'text':'735','left':92,'top':131,'width':42,'height':19}
        cases=([],[dict(native,text='736')],[dict(native,left=220)],
               [dict(native,top=231)],[dict(native,left=25)],
               [native,copy.deepcopy(native)])
        for words in cases:
            with self.subTest(words=words):
                result,calls=self.recognize(native=words)
                self.assertFalse(result['selection_header_ocr']['ok'])
                self.assertEqual(len(calls),1)
                self.assertFalse(result['backup_ocr']['new_member_retry']['verified'])

    def test_local_padding_never_crosses_search_or_header_edge(self):
        for native in ([{'text':'735','left':170,'top':131,'width':7,'height':19}],
                       [{'text':'735','left':92,'top':97,'width':42,'height':19}]):
            result,calls=self.recognize(native=native)
            self.assertFalse(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),1)
            self.assertIn('越界',result['backup_ocr']['new_member_retry']['error'])

    def test_non_gray_crop_and_unavailable_native_ocr_stop_before_extra_recognition(self):
        result,calls=self.recognize(background=(255,255,255))
        self.assertFalse(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),1)
        result,calls=self.recognize(mutate=lambda r:r['ocr'].update(available=False))
        self.assertFalse(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),1)

    def test_engine_error_is_saved_without_further_attempt(self):
        result,calls=self.recognize(engine_error='recognizer unavailable')
        self.assertFalse(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),2)
        self.assertEqual(result['backup_ocr']['new_member_retry']['error'],'recognizer unavailable')

    def test_conflicting_label_at_native_bounds_stops_after_one_local_read(self):
        result,calls=self.recognize(ledger=['736'],first=[{'text':'736','score':1.,'box':CHIP_BOX}])
        self.assertFalse(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),2)
        retry=result['backup_ocr']['new_member_retry']
        self.assertFalse(retry['verified']);self.assertIn('识别不一致',retry['error'])

    def test_changed_image_and_invalid_frame_never_pass(self):
        result,calls=self.recognize(change_image=True)
        self.assertFalse(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),2)
        self.assertIn('截图文件改变',result['selection_header_ocr']['error'])
        result,calls=self.recognize(mutate=lambda r:r.update(dialog_runtime_id=''))
        self.assertFalse(result['selection_header_ocr']['ok']);self.assertEqual(calls,[])

    def test_before_checkpoint_does_not_use_new_chip_fallback(self):
        result,calls=self.recognize(stage='before')
        self.assertTrue(result['selection_header_ocr']['ok'])
        self.assertEqual(len(calls),1);self.assertNotIn('new_member_retry',result['backup_ocr'])

    def test_persistent_engine_restores_detection_after_local_recognition(self):
        report,calls,engine,write=self.scene()
        with tempfile.TemporaryDirectory() as folder:
            report['image_path']=str(Path(folder)/'frame.png');write(report['image_path'])
            report['visible_header_checkpoint']={'scope':'visible_pair','ledger':[],'number':'735','stage':'after'}
            with patch('ocr_worker.recognize_header_ink',return_value=([],[])):
                infer_capture(report,engine,lambda _:'test',Image)
                infer_capture(report,engine,lambda _:'test',Image)
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False,True,False])

    def test_recovered_post_click_selects_once_and_uses_one_capture(self):
        self.replay(success=True)

    def test_failed_post_click_does_not_repeat_click_search_capture_or_wait(self):
        self.replay(success=False)

    def replay(self,*,success):
        after,calls,engine,write=self.scene(texts=('735' if success else '736',))
        before=json.loads(FIXTURE.read_text(encoding='utf-8'))['before']
        native_calls=[];clicks=[]
        def native(window,script,payload):
            mode=payload.get('mode',script);native_calls.append(mode)
            if mode=='click_once':
                clicks.append((payload['number'],payload['x'],payload['y']))
                return {'ok':True,'click_sent':True}
            report=copy.deepcopy(after if clicks else before)
            report['image_path']=payload['image_path'];write(report['image_path'])
            return report
        def worker(request):
            if request.get('header_scan_only') is True:
                return infer_capture(request,engine,lambda _:'test',Image)
            def first(crop,**flags):
                self.assertEqual(crop.shape,(336,728,3))
                return SimpleNamespace(txts=['735','last seen recently'],scores=[1.,1.],boxes=[
                    np.array([[142,225],[194,225],[194,255],[142,255]]),
                    np.array([[142,264],[356,264],[356,301],[142,301]])])
            return infer_capture(request,first,lambda _:'test',Image)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'step.png'
            with patch('controls_probe.run_window_script',side_effect=native),\
                 patch('backup_ocr.run_backup_ocr',side_effect=worker),\
                 patch('ocr_worker.recognize_header_ink',return_value=([],[])),\
                 patch('controls_probe.time.sleep') as wait:
                result=select_member_test(WINDOW,path,'735',expected_selected=[],fast_visible=True)
            self.assertEqual(result,json.loads(path.with_suffix('.json').read_text()))
        self.assertEqual(result['selection_verified'],success,result['reason'])
        self.assertEqual(native_calls,['prepare','click_once','inspect_member_visual.ps1'])
        self.assertEqual(clicks,[('735',832,338)])
        self.assertEqual(len(result['verification_reads']),1)
        self.assertEqual(len(calls),2);wait.assert_called_once_with(.2)
        self.assertFalse(result['final_invite_clicked'])
        self.assertEqual(result['after']['backup_ocr']['new_member_retry']['verified'],success)
        if success:
            self.assertTrue(verify_visible_member_selection(result['before'],result['after'],'735',[])['selection_verified'])
        else:self.assertNotIn('连续两次',result['reason'])


if __name__=='__main__':unittest.main()
