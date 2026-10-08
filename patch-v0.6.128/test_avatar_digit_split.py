"""Regression for portrait ink absorbing an adjacent selected numeric label.

Pixels are reproduced from the displayed 735 image's layout, not its original
PNG bytes. Detector misses are replayed from the report; rec-only integration
tests below run the shipped, hash-checked OCR models when available.
"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image,ImageDraw,ImageFont
import numpy as np

from backup_ocr import apply_visible_header
from controls_probe import select_member_test
from ocr_worker import header_words,recognize_header_ink_uncached,infer_capture,runtime


BASE=Path(__file__).parent
FIXTURE=BASE/'test_fixtures'/'post-click-735-empty.json'
WINDOW={'hwnd':100,'pid':200,'path':r'C:\test\Telegram.exe'}
FONT=next((p for p in (Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    Path(r'C:\Windows\Fonts\segoeui.ttf')) if p.is_file()),None)
HAS_CV=importlib.util.find_spec('cv2') is not None
HAS_MODEL=all(importlib.util.find_spec(name) is not None for name in ('rapidocr','onnxruntime')) and (BASE/'ocr_models'/'manifest.json').is_file()


def scene(number='735',*,avatar=True,score=1.,blank=False,offset=0,query=False):
    report=json.loads(FIXTURE.read_text(encoding='utf-8'))['after']
    report.update(header_page=True,header_scan_only=True)
    image=Image.new('RGB',(728,1160),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(str(FONT),20)
    x=91+offset;right=int(draw.textbbox((x,127),number,font=font)[2])+25
    draw.rounded_rectangle((16+offset,108,right,172),radius=32,fill=(241,241,241))
    if avatar:
        draw.ellipse((16+offset,108,80+offset,172),fill=(45,195,235))
        # Connected portrait-like ink is taller than the text and close enough
        # that the old max-height gap merges it with every digit.
        draw.rectangle((28+offset,112,73+offset,167),fill=(30,30,30))
    if not blank:draw.text((x,127),number,font=font,fill=(30,30,30))
    if query:
        report['regions']['search'].update(left=748+offset/2,width=364-offset/2)
    return report,image


@unittest.skipUnless(HAS_CV and FONT is not None,'Requires OpenCV and a system font')
class AvatarDigitSplitTests(unittest.TestCase):
    def recognize(self,number='735',*,score=1.,**kwargs):
        report,image=scene(number,**kwargs);calls=[]
        def engine(crop,**flags):
            self.assertEqual(flags,{'use_det':False,'use_cls':False,'use_rec':True})
            calls.append(crop.copy())
            return SimpleNamespace(txts=[number],scores=[score])
        extra,diagnostics=recognize_header_ink_uncached(report,image,engine)
        return header_words(report,image,extra),diagnostics,calls

    def test_large_dark_portrait_does_not_absorb_735(self):
        summary,diagnostics,calls=self.recognize()
        self.assertEqual(summary['selected_numbers'],['735']);self.assertEqual(len(calls),1)
        self.assertGreater(min(x for x,y in diagnostics[0]['box']),80)
        self.assertLess(calls[0].shape[0],30)
        self.assertGreaterEqual(summary['accepted'][0]['pale_gray_border'],.8)
        self.assertFalse(summary['final_invite_clicked'])

    def test_varied_numeric_names_keep_whole_glyph_groups(self):
        for number in ('1','12','001','1000','735','123456'):
            with self.subTest(number=number):
                # Longer labels need their actual bound search position.
                report,image=scene(number)
                report['regions']['search'].update(left=950,width=162)
                def engine(crop,**flags):return SimpleNamespace(txts=[number],scores=[1.])
                extra,diagnostics=recognize_header_ink_uncached(report,image,engine)
                self.assertEqual(header_words(report,image,extra)['selected_numbers'],[number])
                self.assertEqual(len(diagnostics),1)

    def test_portrait_without_digits_does_not_call_recognizer(self):
        summary,diagnostics,calls=self.recognize(blank=True)
        self.assertEqual(summary['selected_numbers'],[]);self.assertEqual(diagnostics,[])
        self.assertEqual(calls,[])

    def test_query_digits_remain_excluded(self):
        summary,diagnostics,calls=self.recognize(query=True)
        self.assertEqual(summary['selected_numbers'],[]);self.assertEqual(calls,[])

    def test_80_percent_boundary_is_unchanged(self):
        for score,expected in ((.80,['735']),(.79,[])):
            with self.subTest(score=score):
                summary,diagnostics,calls=self.recognize(score=score)
                self.assertEqual(summary['selected_numbers'],expected);self.assertEqual(len(calls),1)


@unittest.skipUnless(HAS_CV and HAS_MODEL and FONT is not None,'Requires the installed OCR models and environment')
class AvatarDigitRealModelTests(unittest.TestCase):
    def test_shipped_models_recognize_reproduced_735_without_native_word(self):
        report,image=scene();engine,version,Image=runtime()
        extra,diagnostics=recognize_header_ink_uncached(report,image,engine)
        summary=header_words(report,image,extra)
        self.assertEqual(summary['selected_numbers'],['735']);self.assertEqual(len(diagnostics),1)
        self.assertGreaterEqual(summary['accepted'][0]['score'],.80)
        self.assertFalse(any(w['text']=='735' for w in report['ocr']['words']))

    def test_shipped_models_and_bound_guards_recover_first_read_without_native_crop_retry(self):
        report,image=scene();engine,version,Image=runtime();calls=[]
        def missed_detector(crop,**flags):
            calls.append(flags['use_det'])
            if flags['use_det']:return SimpleNamespace(txts=[],scores=[],boxes=[])
            return engine(crop,**flags)
        with tempfile.TemporaryDirectory() as folder:
            report['image_path']=str(Path(folder)/'frame.png');image.save(report['image_path'])
            with patch('backup_ocr.run_backup_ocr',side_effect=lambda r:infer_capture(r,missed_detector,version,Image)):
                result=apply_visible_header(report,[],'735','after')
        self.assertTrue(result['selection_header_ocr']['ok'],result['selection_header_ocr'])
        self.assertEqual(result['backup_ocr']['selected_numbers'],['735'])
        self.assertNotIn('new_member_retry',result['backup_ocr'])
        self.assertEqual(calls,[True,False])

    def test_real_digit_recognition_finishes_step_with_one_post_capture_and_click(self):
        fixture=json.loads(FIXTURE.read_text(encoding='utf-8'));engine,version,Image=runtime()
        native_calls=[];model_calls=[]
        def native(window,script,payload):
            mode=payload.get('mode',script);native_calls.append(mode)
            if mode=='click_once':return {'ok':True,'click_sent':True}
            after=mode=='inspect_member_visual.ps1'
            report,image=scene() if after else (copy.deepcopy(fixture['before']),Image.new('RGB',(728,1160),'white'))
            report['image_path']=payload['image_path'];image.save(report['image_path'])
            return report
        def worker(report):
            def recognize(crop,**flags):
                model_calls.append(flags['use_det'])
                if not flags['use_det']:return engine(crop,**flags)
                if report.get('header_scan_only'):return SimpleNamespace(txts=[],scores=[],boxes=[])
                return SimpleNamespace(txts=['735','last seen recently'],scores=[1.,1.],boxes=[
                    np.array([[142,225],[194,225],[194,255],[142,255]]),
                    np.array([[142,264],[356,264],[356,301],[142,301]])])
            return infer_capture(report,recognize,version,Image)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'step.png'
            with patch('controls_probe.run_window_script',side_effect=native),\
                 patch('backup_ocr.run_backup_ocr',side_effect=worker),patch('controls_probe.time.sleep') as wait:
                result=select_member_test(WINDOW,path,'735',expected_selected=[],fast_visible=True)
            self.assertEqual(result,json.loads(path.with_suffix('.json').read_text()))
        self.assertTrue(result['selection_verified'],result['reason'])
        self.assertEqual(native_calls,['prepare','click_once','inspect_member_visual.ps1'])
        self.assertEqual(model_calls,[True,True,False])
        self.assertEqual(len(result['verification_reads']),1);self.assertNotIn('post_click_retry',result)
        wait.assert_called_once_with(.2)
        self.assertFalse(result['final_invite_clicked'])


if __name__=='__main__':unittest.main()
