"""717 report geometry: one real header crop after a missed selected 716."""
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
from ocr_worker import infer_capture,header_crop_bounds
from visual_members import analyze_member_visual,plan_member_selection


FIXTURE=Path(__file__).parent/'test_fixtures'/'previous-member-717.json'
WINDOW={'hwnd':100,'pid':200,'path':r'C:\test\Telegram.exe'}


class PreviousMemberRetryTests(unittest.TestCase):
    def scene(self,*,first=('715',),second=('715','716'),first_score=1.,second_score=1.,
              row='717',second_error=None,change_image=False):
        report=json.loads(FIXTURE.read_text(encoding='utf-8'))['before']
        for word in report['ocr']['words']:
            if word['text']=='717' and word['top']>=184:word['text']=row
        calls=[]
        boxes=([[84,121],[140,121],[140,159],[84,159]],
               [[242,121],[299,121],[299,159],[242,159]])
        def write(r,path):
            image=Image.new('RGB',(728,1160),'white');draw=ImageDraw.Draw(image)
            for box in boxes:
                draw.rectangle((box[0][0],box[0][1],box[2][0],box[2][1]),fill=(241,241,241))
                draw.rectangle((box[0][0]+7,box[0][1]+7,box[0][0]+14,box[2][1]-7),fill=(40,40,40))
            # Dark query ink must disappear from the retry's actual input.
            draw.rectangle((350,130,365,146),fill=(0,0,0))
            draw.rectangle((20,240,30,250),fill=(200,20,20))
            image.save(path)
        def engine(crop,**flags):
            self.assertEqual(flags,{'use_det':True,'use_cls':False,'use_rec':True})
            if crop.shape==(336,728,3):
                bounds=(0,0,728,336);names=first;score=first_score;kind='first_row'
            elif crop.shape==(88,728,3):
                bounds=(0,96,728,184);names=second;score=second_score;kind='header_retry'
                if second_error is not None:
                    calls.append({'kind':kind,'shape':crop.shape,'pixels':crop.copy()})
                    raise ValueError(second_error)
            else:raise AssertionError('Unexpected OCR crop '+str(crop.shape))
            calls.append({'kind':kind,'shape':crop.shape,'pixels':crop.copy()})
            detections=[(name,score,boxes[i]) for i,name in enumerate(names)]
            if kind=='first_row':
                detections += [(row,1.,[[143,225],[195,225],[195,255],[143,255]]),
                    ('last seen within a week',1.,[[142,263],[430,263],[430,300],[142,300]])]
            if kind=='header_retry' and change_image:
                Path(report['image_path']).write_bytes(b'changed frame')
            return SimpleNamespace(txts=[d[0] for d in detections],scores=[d[1] for d in detections],
                boxes=[np.array([[x-bounds[0],y-bounds[1]] for x,y in d[2]]) for d in detections])
        return report,calls,engine,write

    def recognize(self,**kwargs):
        report,calls,engine,write=self.scene(**kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            report['image_path']=str(Path(tmp)/'frame.png');write(report,report['image_path'])
            def worker(request):
                try:return infer_capture(request,engine,lambda _:'test',Image)
                except Exception as error:
                    return {'ok':False,'error':str(error),'final_invite_clicked':False}
            with patch('backup_ocr.run_backup_ocr',side_effect=worker),\
                 patch('ocr_worker.recognize_header_ink',return_value=([],[])):
                result=apply_visible_header(report,['715','716'],'717','before')
        return result,calls

    def test_report_717_retries_only_header_then_plans_first_row(self):
        result,calls=self.recognize()
        self.assertTrue(result['selection_header_ocr']['ok'],result['selection_header_ocr'])
        retry=result['backup_ocr']['previous_member_retry']
        self.assertTrue(retry['verified']);self.assertEqual(retry['number'],'716')
        self.assertEqual(retry['first']['selected_numbers'],['715'])
        self.assertEqual(retry['second']['selected_numbers'],['715','716'])
        self.assertEqual(retry['crop_bounds'],[0,96,728,184])
        self.assertEqual([c['kind'] for c in calls],['first_row','header_retry'])
        self.assertEqual(plan_member_selection(result,'717',['715','716'])['y'],338)
        self.assertEqual(len(result['backup_ocr']['member_row_scan']['scans']),1)

    def test_retry_input_contains_header_pixels_and_masks_live_query(self):
        result,calls=self.recognize()
        retry=calls[1]['pixels']
        self.assertFalse(np.any(np.all(retry==(200,20,20),axis=2)))
        self.assertTrue(np.all(retry[34:51,350:366]==255))
        self.assertTrue(np.any(np.all(retry[25:64,242:300]==(40,40,40),axis=2)))
        self.assertTrue(result['backup_ocr']['previous_member_retry']['second']['header_search_masked'])

    def test_missing_previous_after_retry_stops_with_both_reads_saved(self):
        result,calls=self.recognize(second=('715',))
        self.assertFalse(result['selection_header_ocr']['ok'])
        self.assertEqual([c['kind'] for c in calls],['first_row','header_retry'])
        retry=result['backup_ocr']['previous_member_retry']
        self.assertFalse(retry['verified']);self.assertEqual(retry['second']['selected_numbers'],['715'])
        self.assertIn('顶部复核仍未通过',result['selection_header_ocr']['error'])
        with self.assertRaises(ValueError):plan_member_selection(result,'717',['715','716'])

    def test_successful_first_header_does_not_add_a_retry(self):
        result,calls=self.recognize(first=('715','716'))
        self.assertTrue(result['selection_header_ocr']['ok'])
        self.assertEqual([c['kind'] for c in calls],['first_row'])
        self.assertNotIn('previous_member_retry',result['backup_ocr'])

    def test_unknown_duplicate_or_low_score_initial_header_does_not_retry(self):
        for first,score in [(('715','999'),1.),(('716','716'),1.),(('715','716'),.79)]:
            with self.subTest(first=first,score=score):
                result,calls=self.recognize(first=first,first_score=score)
                self.assertFalse(result['selection_header_ocr']['ok'])
                self.assertEqual([c['kind'] for c in calls],['first_row'])

    def test_wrong_duplicate_or_low_confidence_retry_cannot_pass(self):
        for second,score in [(('715','999'),1.),(('716','716'),1.),(('715','716'),.79)]:
            with self.subTest(second=second,score=score):
                result,calls=self.recognize(second=second,second_score=score)
                self.assertFalse(result['selection_header_ocr']['ok'])
                self.assertEqual(len(calls),2)
                self.assertFalse(result['backup_ocr']['previous_member_retry']['verified'])

    def test_retry_engine_error_is_saved_and_has_no_third_attempt(self):
        result,calls=self.recognize(second_error='OCR unavailable')
        self.assertFalse(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),2)
        self.assertEqual(result['backup_ocr']['previous_member_retry']['second']['error'],'OCR unavailable')

    def test_changed_image_during_retry_does_not_pass(self):
        result,calls=self.recognize(change_image=True)
        self.assertFalse(result['selection_header_ocr']['ok']);self.assertEqual(len(calls),2)
        self.assertIn('截图文件改变',result['selection_header_ocr']['error'])

    def test_corrected_header_does_not_supply_a_missing_list_match(self):
        result,calls=self.recognize(row='718')
        self.assertTrue(result['selection_header_ocr']['ok'])
        self.assertEqual(len(calls),2)
        self.assertEqual(analyze_member_visual(result,'717')['list_matches'],0)
        with self.assertRaises(ValueError):plan_member_selection(result,'717',['715','716'])

    def test_failed_retry_never_requests_physical_click_or_second_search(self):
        self.replay(second=('715',),success=False)

    def test_recovered_retry_sends_one_click_and_verifies_new_717(self):
        self.replay(success=True)

    def replay(self,*,second=('715','716'),success):
        initial,calls,engine,write=self.scene(second=second)
        native_calls=[];clicks=[]
        def native(window,script,payload):
            mode=payload.get('mode',script);native_calls.append(mode)
            if mode=='click_once':
                clicks.append((payload['number'],payload['x'],payload['y']))
                return {'ok':True,'click_sent':True}
            self.assertTrue(payload['first_row_scan'])
            report=copy.deepcopy(initial)
            if clicks:
                self.assertTrue(payload['header_scan_only'])
                report['regions']['search'].update(left=990,width=112)
                report['header_scroll']['search_value']=''
                report['header_scan_only']=True
            report['image_path']=payload['image_path'];write(report,report['image_path'])
            if clicks:
                with Image.open(report['image_path']) as image:
                    draw=ImageDraw.Draw(image);draw.rectangle((405,121,465,159),fill=(241,241,241))
                    draw.rectangle((412,128,420,152),fill=(40,40,40));image.save(report['image_path'])
            return report
        def worker(request):
            if request.get('header_scan_only') is True:
                def after(crop,**flags):
                    self.assertEqual(crop.shape,(88,728,3))
                    bounds=header_crop_bounds(request,728,1160)
                    boxes=[[[84,121],[140,121],[140,159],[84,159]],
                        [[242,121],[299,121],[299,159],[242,159]],
                        [[405,121],[465,121],[465,159],[405,159]]]
                    return SimpleNamespace(txts=['715','716','717'],scores=[1.,1.,1.],
                        boxes=[np.array([[x-bounds[0],y-bounds[1]] for x,y in box]) for box in boxes])
                return infer_capture(request,after,lambda _:'test',Image)
            return infer_capture(request,engine,lambda _:'test',Image)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'step.png'
            with patch('controls_probe.run_window_script',side_effect=native),\
                 patch('backup_ocr.run_backup_ocr',side_effect=worker),\
                 patch('ocr_worker.recognize_header_ink',return_value=([],[])),patch('controls_probe.time.sleep'):
                result=select_member_test(WINDOW,path,'717',expected_selected=['715','716'],fast_visible=True)
            self.assertEqual(result,json.loads(path.with_suffix('.json').read_text()))
        self.assertEqual(result['selection_verified'],success,result['reason'])
        self.assertEqual(native_calls,['prepare','click_once','inspect_member_visual.ps1'] if success else ['prepare'])
        self.assertEqual(clicks,[('717',832,338)] if success else [])
        self.assertEqual([c['kind'] for c in calls],['first_row','header_retry'])
        self.assertFalse(result['final_invite_clicked'])


if __name__=='__main__':unittest.main()
