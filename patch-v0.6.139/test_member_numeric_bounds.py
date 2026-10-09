"""Bound OCR regions replace ink-band counting; no Windows input is sent."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image

from member_numeric_bounds import numeric_guard
from ocr_worker import recognize_member_crop, infer_capture, runtime
import test_premium_member_star as premium
import test_target_only_members as target
from test_rapid_list import detection
from visual_members import plan_member_selection

BASE=Path(__file__).parent


def merged_frame():
    r=premium.candidate_frame('1027 🙂')
    r['ocr']['words']=[w for w in r['ocr']['words'] if w['top']<304]
    parent=r['backup_ocr']['detections'][0]
    # Recognition character cells in the same parent line, before the suffix.
    chars=[detection(c,(143+14*i,341,157+14*i,376),.95) for i,c in enumerate('1027🙂')]
    r['backup_ocr']['member_character_boxes']=[dict(parent_text=parent['text'],parent_box=parent['box'],characters=chars)]
    return r


class NumericOCRBoundsTests(unittest.TestCase):
    def test_original_2416_report_uses_actual_windows_box_without_ink_splitting(self):
        r=json.loads((BASE/'test_fixtures/numeric-guard-2416.json').read_text())
        plan=plan_member_selection(r,'2416',[]);guard=numeric_guard(r,plan)
        self.assertEqual((plan['x'],plan['y']),(836,338))
        self.assertEqual(guard,dict(numeric_bounds=dict(left=822.,top=333.5,width=28.,height=9.5),
            numeric_text='2416',numeric_source='windows_numeric'))

    def test_exact_rapid_box_is_used_when_windows_missed_the_name(self):
        r=premium.candidate_frame('1027')
        r['ocr']['words']=[w for w in r['ocr']['words'] if w['top']<304]
        plan=plan_member_selection(r,'1027',[]);guard=numeric_guard(r,plan)
        self.assertEqual(guard['numeric_source'],'rapid_numeric')
        self.assertEqual(guard['numeric_bounds'],plan['name_bounds'])

    def test_merged_number_and_emoji_uses_only_recognized_prefix_characters(self):
        r=merged_frame();plan=plan_member_selection(r,'1027',[])
        self.assertEqual(numeric_guard(r,plan),dict(numeric_text='1027',numeric_source='rapid_numeric_characters',
            numeric_bounds=dict(left=819.5,top=388.5,width=28.,height=17.5)))
        self.assertGreater(plan['name_bounds']['width'],28.)

    def test_character_boxes_do_not_add_a_confidence_threshold_to_accepted_names(self):
        r=merged_frame();r['backup_ocr']['member_character_boxes'][0]['characters'][1]['score']=.79
        self.assertEqual(numeric_guard(r,plan_member_selection(r,'1027',[]))['numeric_source'],'rapid_numeric_characters')

    def test_wrong_parent_row_incomplete_digits_and_changed_frame_are_rejected(self):
        mutations=[lambda r:r['backup_ocr']['member_character_boxes'][0].update(parent_text='10270 🙂'),
            lambda r:r['backup_ocr']['member_character_boxes'][0]['characters'][0].update(text='9'),
            lambda r:r['backup_ocr']['member_character_boxes'][0]['characters'].pop(0),
            lambda r:r['backup_ocr']['member_character_boxes'][0]['characters'][0].update(box=detection('1',(10,341,24,376))['box']),
            lambda r:r['backup_ocr']['member_character_boxes'][0]['characters'][0].update(score=float('nan')),
            lambda r:r['selection_header_ocr'].update(image_sha256='a'*64),
            lambda r:r.update(window_handle=999)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                r=merged_frame();plan=plan_member_selection(r,'1027',[]);mutation(r)
                with self.assertRaises(ValueError):numeric_guard(r,plan)

    def test_absent_numeric_coordinates_do_not_use_a_guessed_width(self):
        r=merged_frame();r['backup_ocr'].pop('member_character_boxes')
        with self.assertRaisesRegex(ValueError,'缺少独立'):numeric_guard(r,plan_member_selection(r,'1027',[]))

    def test_target_character_options_restore_after_success_and_failure(self):
        for fail in (False,True):
            seen=[]
            def engine(pixels,**flags):
                seen.append(flags)
                engine.return_word_box=True;engine.return_single_char_box=True
                if fail:raise ValueError('OCR failed')
                return SimpleNamespace()
            engine.return_word_box=False;engine.return_single_char_box=False
            if fail:
                with self.assertRaises(ValueError):recognize_member_crop(engine,np.zeros((20,100,3)),target_only=True)
            else:recognize_member_crop(engine,np.zeros((20,100,3)),target_only=True)
            self.assertTrue(seen[0]['return_word_box']);self.assertTrue(seen[0]['return_single_char_box'])
            self.assertFalse(engine.return_word_box);self.assertFalse(engine.return_single_char_box)

    def test_pipeline_binds_emoji_prefix_without_any_extra_search_ocr_or_wait(self):
        r,native,calls,wait=target.TargetOnlyTests().replay(names=('737 🙂🔥','737 bot','737'))
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual([c[0] for c in native],['prepare','click_once','inspect_member_visual.ps1'])
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False])
        payload=native[1][1]
        self.assertEqual(payload['numeric_text'],'737')
        self.assertEqual(payload['numeric_source'],'rapid_numeric_characters')
        self.assertLess(payload['numeric_bounds']['width'],payload['name_bounds']['width'])
        wait.assert_called_once_with(.2)

    @unittest.skipUnless(target.HAS_MODEL,'Requires shipped OCR models')
    def test_real_ocr_retains_character_coordinates_on_the_same_target_pass(self):
        engine,version,_=runtime();r=premium.native_frame()
        original=(engine.return_word_box,engine.return_single_char_box)
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            r['image_path']=str(Path(folder)/'row.png');premium.pixels(r).save(r['image_path'])
            r.update(target_member_only=True,target_only_checkpoint={'number':'1027','stage':'before'},
                progressive_member_ocr=True,member_row_target='1027')
            result=infer_capture(r,engine,version,Image)
        lines=[line for line in result['member_character_boxes'] if line['parent_text'].startswith('1027')]
        self.assertTrue(lines,result.get('detections'))
        self.assertEqual(''.join(c['text'] for c in lines[0]['characters'][:4]),'1027')
        self.assertEqual((engine.return_word_box,engine.return_single_char_box),original)


if __name__=='__main__':unittest.main()
