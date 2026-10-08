"""Premium badges beside exact numeric names, using actual1027 OCR evidence.

The JSON has no PNG bytes. Native actions are mocked; model integration
recognizes reproduced row/chip pixels with the real checksum-verified models.
"""
import copy
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image,ImageDraw,ImageFont

from controls_probe import select_member_test
from member_batch import select_members_test
from member_row_scan import member_row_crops
from ocr_worker import infer_capture,runtime
import test_target_only_members as target_tests
from test_rapid_list import detection
from visual_members import analyze_member_visual,plan_member_selection
from window_queue import ready_for_manual_invite

FIXTURE=Path(__file__).parent/'test_fixtures'/'premium-star-1027.json'


def fixture():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def candidate_frame(label='1027 ★',*,split=False,score=.88483,badge_score=.99):
    r=fixture()['before']
    scan=r['backup_ocr']['member_row_scan']['scans'][0]
    words=copy.deepcopy(scan['detections'])
    words[0].update(text=label,score=score)
    if split:
        words[0]=detection(label,(143,341,205,376),score)
        words.append(detection('★',(215,342,241,375),badge_score))
    r['backup_ocr']['detections']=words
    r['backup_ocr']['member_row_scan'].update(matched_row=1,crop_bounds=scan['crop_bounds'])
    return r


def native_frame():
    r=fixture()['before']
    for key in ('selection_header_ocr','backup_ocr','target_only_checkpoint',
                'progressive_member_ocr','member_row_target','header_page'):
        r.pop(key,None)
    return r


def pixels(r,*,post=False,label='1027',star=True):
    image=Image.new('RGB',(728,1160),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(str(target_tests.FONT),24)
    if post:
        draw.rounded_rectangle((16,184,184,240),radius=28,fill=(241,241,241))
        draw.ellipse((16,184,72,240),fill=(246,165,63))
        draw.text((88,198),label,font=font,fill=(30,30,30))
    else:
        draw.ellipse((18,328,82,392),fill=(165,165,165))
        draw.text((149,338),label,font=font,fill=(25,25,25))
        if star:
            points=[]
            for i in range(10):
                angle=-math.pi/2+i*math.pi/5;radius=12 if i%2==0 else 5
                points.append((231+radius*math.cos(angle),353+radius*math.sin(angle)))
            draw.polygon(points,fill=(48,174,225))
        draw.text((149,382),'last seen 11 minutes ago',
                  font=ImageFont.truetype(str(target_tests.FONT),20),fill=(145,145,145))
    return image


class PremiumMemberStarTests(unittest.TestCase):
    def test_actual_1027_report_now_plans_the_first_row(self):
        r=candidate_frame()
        a=analyze_member_visual(r,'1027')
        self.assertTrue(a['usable'],a['reason']);self.assertEqual(a['list_matches'],1)
        plan=plan_member_selection(r,'1027',[])
        self.assertEqual((plan['x'],plan['y']),(844,397))
        self.assertEqual(r['backup_ocr']['detections'][0]['text'],'1027 ★')
        self.assertEqual(r['backup_ocr']['detections'][0]['score'],.88483)

    def test_trailing_premium_stars_are_optional(self):
        for label in ('1027★','1027 ★','1027\t★','1027 ⭐','1027 ⭐\ufe0f','1027 ★\ufe0f','1027 ★★'):
            with self.subTest(label=label):
                self.assertEqual(analyze_member_visual(candidate_frame(label),'1027')['list_matches'],1)

    def test_other_actual_names_remain_mismatches(self):
        for label in ('10270 ★','01027 ★','E 1027 ★','1027 bot ★','1027★bot',
                      '1027 ★ 1','★1027','I027 ★','1027. ★','1027 会'):
            with self.subTest(label=label):
                self.assertEqual(analyze_member_visual(candidate_frame(label),'1027')['list_matches'],0)

    def test_split_premium_badge_matches_complete_numeric_word(self):
        r=candidate_frame('1027',split=True)
        self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],1)
        self.assertEqual(plan_member_selection(r,'1027',[])['x'],835)

    def test_split_badge_must_still_be_to_the_right(self):
        for box in ((110,342,136,375),(180,342,206,375)):
            with self.subTest(box=box):
                r=candidate_frame('1027',split=True)
                r['backup_ocr']['detections'][-1]=detection('★',box)
                self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],0)

    def test_split_badge_confidence_does_not_block_a_good_number(self):
        r=candidate_frame('1027',split=True,badge_score=.79)
        self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],1)

    def test_extra_text_on_name_line_still_blocks_selection(self):
        for text in ('bot','KREPEZ','1'):
            with self.subTest(text=text):
                r=candidate_frame()
                r['backup_ocr']['detections'].append(detection(text,(245,341,280,376)))
                self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],0)

    def test_status_anchor_and_capture_binding_are_still_required(self):
        r=candidate_frame();r['backup_ocr']['detections']=r['backup_ocr']['detections'][:1]
        self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],0)
        r=candidate_frame();r['selection_header_ocr']['image_sha256']='b'*64
        self.assertFalse(analyze_member_visual(r,'1027')['usable'])

    def test_clipped_badge_name_cannot_become_a_click_candidate(self):
        r=candidate_frame()
        r['backup_ocr']['detections'][0]=detection('1027 ★',(143,302,243,340))
        self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],0)

    def test_windows_merged_or_separate_star_keeps_complete_name_matching(self):
        for split in (False,True):
            with self.subTest(split=split):
                r=candidate_frame('Other')
                r['ocr']['words']=[w for w in r['ocr']['words'] if w['top']<304]
                r['ocr']['words'].append({'text':'1027' if split else '1027 ★',
                    'left':149,'top':351,'width':55 if split else 92,'height':19})
                if split:r['ocr']['words'].append({'text':'★','left':214,'top':348,'width':23,'height':22})
                self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],1)

    def test_low_confidence_star_name_still_needs_two_bound_good_reads(self):
        r=candidate_frame(score=.79)
        self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],0)
        self.assertFalse(analyze_member_visual(r,'1027')['usable'])

    def test_first_premium_row_does_not_read_later_similar_results(self):
        r,native,calls,wait=target_tests.TargetOnlyTests().replay(
            names=('737 ★','737 bot','737'))
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False])
        self.assertEqual([c[0] for c in native],['prepare','click_once','inspect_member_visual.ps1'])
        self.assertEqual(r['before']['backup_ocr']['member_row_scan']['matched_row'],1)
        self.assertEqual(len(r['verification_reads']),1);wait.assert_called_once_with(.2)

    def test_second_premium_row_is_used_after_first_name_mismatch(self):
        r,_,calls,_=target_tests.TargetOnlyTests().replay(names=('E 737 ★','737 ★','737'))
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(r['before']['backup_ocr']['member_row_scan']['matched_row'],2)
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,True,False])

    def test_third_premium_row_is_used_after_two_name_mismatches(self):
        r,_,calls,_=target_tests.TargetOnlyTests().replay(names=('7370 ★','E 737','737 ★'))
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(r['before']['backup_ocr']['member_row_scan']['matched_row'],3)
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,True,True,False])

    def test_header_chip_does_not_gain_relaxed_name_rules(self):
        r,_,_,_=target_tests.TargetOnlyTests().replay(texts=('737 ★',))
        self.assertFalse(r['selection_verified'])
        self.assertEqual(len(r['verification_reads']),2)

    def replay_1027(self,*,score=.88483,reads=('1027 ★','1027 ★'),
                    read_scores=(.94,.94),real_model=False,number='1027',ledger=None):
        native_calls=[];ocr_calls=[]
        source=fixture()['before']['backup_ocr']['member_row_scan']['scans'][0]['detections']
        def native(window,script,payload):
            mode=payload.get('mode',script);native_calls.append(mode)
            if mode=='click_once':return {'ok':True,'click_sent':True,'click_attempted':True}
            r=native_frame();post=mode=='inspect_member_visual.ps1'
            r['search_preparation']['number']=number
            r['header_scroll']['search_value']=number
            for word in r['ocr']['words']:
                if word['text']=='1027':word['text']=number
            if post:
                r['header_scroll']['search_value']=''
                r['regions']['search'].update(left=960,width=102)
            r['image_path']=payload['image_path'];pixels(r,post=post,label=number).save(r['image_path'])
            return r
        def worker(report):
            rec_index=0
            def engine(crop,**flags):
                nonlocal rec_index
                stage=report['target_only_checkpoint']['stage']
                ocr_calls.append({'stage':stage,'flags':flags,'shape':crop.shape})
                if real_model:
                    model,_,_=runtime()
                    return model(crop,**flags)
                if stage=='after':return SimpleNamespace(txts=[number],scores=[.999])
                if not flags['use_det']:
                    index=rec_index;rec_index+=1
                    return SimpleNamespace(txts=[reads[index]],scores=[read_scores[index]])
                bounds=member_row_crops(report,728,1160)[0]
                self.assertEqual(crop.shape[:2],(bounds[3]-bounds[1],bounds[2]-bounds[0]))
                records=copy.deepcopy(source);records[0].update(text=number+' ★',score=score)
                return SimpleNamespace(txts=[w['text'] for w in records],scores=[w['score'] for w in records],
                    boxes=[np.array([[x-bounds[0],y-bounds[1]] for x,y in w['box']]) for w in records])
            return infer_capture(report,engine,lambda _:'offline-test',Image)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'1027.png'
            with patch('controls_probe.run_window_script',side_effect=native),\
                 patch('backup_ocr.run_backup_ocr',side_effect=worker),\
                 patch('controls_probe.time.sleep') as waits:
                result=select_member_test(target_tests.WINDOW,path,number,
                    expected_selected=fixture()['expected_selected'] if ledger is None else ledger,
                    fast_visible=True,target_only=True)
            self.assertEqual(result,json.loads(path.with_suffix('.json').read_text()))
        return result,native_calls,ocr_calls,waits

    def test_actual_1027_ocr_enters_one_click_and_first_post_read(self):
        r,native,calls,waits=self.replay_1027()
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(native,['prepare','click_once','inspect_member_visual.ps1'])
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False])
        self.assertEqual(len(r['verification_reads']),1);waits.assert_called_once_with(.2)
        self.assertEqual(r['before']['backup_ocr']['detections'][0]['text'],'1027 ★')

    def test_low_score_star_crop_can_recover_with_two_good_exact_reads(self):
        r,native,calls,_=self.replay_1027(score=.79,reads=('1027 ★','1027'))
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(native.count('click_once'),1)
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False,False,False])
        reviews=r['before']['backup_ocr']['list_name_reviews']
        self.assertEqual([v['text'] for v in reviews[0]['reads']],['1027 ★','1027'])

    def test_low_score_star_with_conflicting_or_weak_reads_never_clicks(self):
        for reads,scores in ((('1027 ★','1028 ★'),(.94,.94)),
                            (('1027 ★','1027 ★'),(.79,.94)),
                            (('1027 bot ★','1027 ★'),(.94,.94))):
            with self.subTest(reads=reads,scores=scores):
                r,native,_,_=self.replay_1027(score=.79,reads=reads,read_scores=scores)
                self.assertFalse(r['selection_verified']);self.assertEqual(native,['prepare'])

    def test_1027_star_success_continues_1028_and_finishes_without_final_reread(self):
        steps=[self.replay_1027(ledger=())[0],
               self.replay_1027(number='1028',ledger=('1027',))[0]]
        self.assertTrue(all(s['selection_verified'] for s in steps))
        calls=[];initial=native_frame()
        initial['analysis']={'usable':True,'selected_numbers':[],'other_header_words':[]}
        def select(window,path,number,**options):
            self.assertEqual(list(options['expected_selected']),['1027','1028'][:len(calls)])
            calls.append(number);return copy.deepcopy(steps[len(calls)-1])
        with tempfile.TemporaryDirectory() as tmp,\
             patch('member_batch.inspect_member_search',return_value=initial),\
             patch('backup_ocr.apply_visible_header',return_value=initial),\
             patch('visual_members.analyze_member_visual',return_value=initial['analysis']),\
             patch('member_batch.select_member_test',side_effect=select),\
             patch('member_batch.inspect_final_member') as final_read:
            r=select_members_test(target_tests.WINDOW,Path(tmp)/'batch.json',
                                  ['1027','1028'],fast_visible=True,target_only=True)
        self.assertTrue(r['selection_verified'],r['reason']);self.assertEqual(calls,['1027','1028'])
        self.assertEqual(r['selected_numbers'],['1027','1028']);self.assertEqual(r['remaining_numbers'],[])
        self.assertEqual(r['final_reads'],[]);final_read.assert_not_called()
        self.assertTrue(ready_for_manual_invite(r,target_tests.WINDOW,['1027','1028']))

    @unittest.skipUnless(target_tests.HAS_MODEL,'real OCR models are not installed')
    def test_real_model_reads_reproduced_blue_star_row_and_selected_chip(self):
        r,native,calls,waits=self.replay_1027(real_model=True)
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(native,['prepare','click_once','inspect_member_visual.ps1'])
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False])
        self.assertEqual(r['after']['backup_ocr']['selected_numbers'],['1027'])
        self.assertEqual(len(r['verification_reads']),1);waits.assert_called_once_with(.2)


if __name__=='__main__':unittest.main()
