"""Numeric member rows ignore trailing icons/emoji without extra OCR/input."""
import unittest
from unittest.mock import patch

from PIL import ImageDraw,ImageFont

import test_premium_member_star as premium
import test_target_only_members as target
from test_rapid_list import detection
from visual_members import analyze_member_visual,plan_member_selection


class MemberIconTests(unittest.TestCase):
    def test_icons_and_emoji_after_complete_target_are_ignored(self):
        for suffix in ('☆','✓','🏦','🙂','🔥','❤️','👩🏽\u200d💻','👨\u200d👩\u200d👧\u200d👦',
                       '🇨🇳','🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f',
                       'ℹ️','☀️','🎵','∞','★ ⭐ 🔥','1️⃣','#️⃣','*️⃣','*'):
            for gap in ('',' '):
                with self.subTest(suffix=suffix,gap=gap):
                    r=premium.candidate_frame('1027'+gap+suffix)
                    a=analyze_member_visual(r,'1027')
                    self.assertTrue(a['usable'],a['reason']);self.assertEqual(a['list_matches'],1)

    def test_actual_words_other_digits_and_incomplete_names_still_fail(self):
        for label in ('10270🙂','01027🔥','E 1027🙂','1027bot🙂','1027 bot🙂',
                      '1027 ★ 1','1027 2 🔥','1027 会','1027 A','1027二','I027🙂',
                      '1027.','1027:','1027\n🙂','1027\u200d','1027\ufe0f'):
            with self.subTest(label=label):
                self.assertEqual(analyze_member_visual(premium.candidate_frame(label),'1027')['list_matches'],0)

    def test_one_to_six_digits_and_explicit_legacy_zeroes_keep_identity(self):
        for name in ('1','12','001','1027','123456'):
            with self.subTest(name=name):
                r=premium.candidate_frame(name+' 🔥')
                r['member_row_target']=name;r['backup_ocr']['member_row_scan']['number']=name
                r['target_only_checkpoint']['number']=name
                r['backup_ocr']['target_stage']='before'
                self.assertEqual(analyze_member_visual(r,name)['list_matches'],1)
                self.assertEqual(plan_member_selection(r,name,[])['number'],name)

    def test_multiple_separate_icon_boxes_follow_numeric_name(self):
        r=premium.candidate_frame('1027',split=True)
        r['backup_ocr']['detections'].extend([
            detection('🙂',(245,341,277,376),.11),
            detection('🔥',(281,341,313,376),.07)])
        self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],1)
        self.assertEqual(plan_member_selection(r,'1027',[])['x'],835)
        self.assertEqual(r['backup_ocr']['detections'][-1]['text'],'🔥')

    def test_icon_score_size_and_spacing_do_not_block_a_good_numeric_name(self):
        for box in ((270,342,296,375),(215,342,275,375),(215,330,241,390)):
            with self.subTest(box=box):
                r=premium.candidate_frame('1027',split=True)
                r['backup_ocr']['detections'][-1]=detection('🙂',box,.05)
                self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],1)

    def test_overlapping_right_icon_boxes_do_not_block_the_numeric_name(self):
        r=premium.candidate_frame('1027',split=True)
        r['backup_ocr']['detections'][-1]=detection('🙂',(202,341,237,376),.11)
        r['backup_ocr']['detections'].append(detection('🔥',(229,341,264,376),.07))
        self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],1)
        self.assertEqual(plan_member_selection(r,'1027',[])['x'],835)

    def test_later_words_or_digits_still_block_a_split_icon_name_line(self):
        for text in ('bot','2','KREPEZ'):
            with self.subTest(text=text):
                r=premium.candidate_frame('1027',split=True)
                r['backup_ocr']['detections'].append(detection(text,(245,341,299,376)))
                self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],0)

    def test_icon_before_or_overlapping_number_is_not_a_trailing_suffix(self):
        for box in ((110,342,136,375),(180,342,206,375)):
            with self.subTest(box=box):
                r=premium.candidate_frame('1027',split=True)
                r['backup_ocr']['detections'][-1]=detection('🙂',box)
                self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],0)

    def test_first_emoji_row_finishes_without_scanning_later_rows(self):
        r,native,calls,wait=target.TargetOnlyTests().replay(names=('737 🙂🔥','737 bot','737'))
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False])
        self.assertEqual([c[0] for c in native],['prepare','click_once','inspect_member_visual.ps1'])
        self.assertEqual(r['before']['backup_ocr']['member_row_scan']['matched_row'],1)
        self.assertEqual(len(r['verification_reads']),1);wait.assert_called_once_with(.2)

    def test_next_row_is_used_only_when_first_number_or_name_does_not_match(self):
        for first in ('7370🙂','737 bot','E 737🔥'):
            with self.subTest(first=first):
                r,_,calls,_=target.TargetOnlyTests().replay(names=(first,'737 👩🏽\u200d💻','737'))
                self.assertTrue(r['selection_verified'],r['reason'])
                self.assertEqual(r['before']['backup_ocr']['member_row_scan']['matched_row'],2)
                self.assertEqual([c['flags']['use_det'] for c in calls],[True,True,False])

    def test_query_changes_still_prevent_click_for_an_emoji_name(self):
        r,native,_,_=target.TargetOnlyTests().replay(names=('737 🙂',),
            mutate_before=lambda r:r['header_scroll'].update(search_value='738'))
        self.assertFalse(r['click_sent']);self.assertEqual([c[0] for c in native],['prepare'])

    def test_selected_chip_rule_remains_exact_numeric(self):
        r,native,_,_=target.TargetOnlyTests().replay(names=('737 🙂',),texts=('737 🙂',))
        self.assertFalse(r['selection_verified']);self.assertEqual(len(r['verification_reads']),2)
        self.assertEqual(sum(c[0]=='click_once' for c in native),1)

    def test_low_score_number_with_emoji_keeps_existing_two_read_threshold(self):
        r,native,calls,_=premium.PremiumMemberStarTests().replay_1027(
            score=.79,reads=('1027 🙂','1027 👩🏽\u200d💻'))
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(native.count('click_once'),1)
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False,False,False])

    def test_low_score_number_does_not_accept_conflicting_numeric_icon_reads(self):
        r,native,_,_=premium.PremiumMemberStarTests().replay_1027(
            score=.79,reads=('1027 🙂','10270 🔥'))
        self.assertFalse(r['selection_verified']);self.assertEqual(native,['prepare'])

    def test_windows_merged_or_separate_emoji_name_is_supported(self):
        for split in (False,True):
            with self.subTest(split=split):
                r=premium.candidate_frame('Other')
                r['ocr']['words']=[w for w in r['ocr']['words'] if w['top']<304]
                r['ocr']['words'].append({'text':'1027' if split else '1027 🙂🔥',
                    'left':149,'top':351,'width':55 if split else 110,'height':19})
                if split:
                    r['ocr']['words'] += [
                        {'text':'🙂','left':214,'top':348,'width':23,'height':22},
                        {'text':'🔥','left':244,'top':348,'width':23,'height':22}]
                self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],1)

    def test_scrambled_detection_order_keeps_the_leftmost_number_and_right_icons(self):
        r=premium.candidate_frame('1027',split=True)
        r['backup_ocr']['detections'].append(detection('🔥',(245,341,277,376),.1))
        r['backup_ocr']['detections'].reverse()
        self.assertEqual(analyze_member_visual(r,'1027')['list_matches'],1)

    @unittest.skipUnless(target.HAS_MODEL,'real OCR models are not installed')
    def test_real_model_reads_numeric_first_row_with_multiple_colored_icons(self):
        original=premium.pixels
        def icon_pixels(r,*,post=False,label='1027',star=True):
            image=original(r,post=post,label=label,star=False)
            if not post:
                draw=ImageDraw.Draw(image);font=ImageFont.truetype(str(target.FONT),25)
                draw.text((219,337),'♥',font=font,fill=(239,85,114))
                draw.text((249,337),'★',font=font,fill=(48,174,225))
            return image
        with patch('test_premium_member_star.pixels',side_effect=icon_pixels):
            r,native,calls,wait=premium.PremiumMemberStarTests().replay_1027(real_model=True)
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(native,['prepare','click_once','inspect_member_visual.ps1'])
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False])
        self.assertEqual(len(r['verification_reads']),1);wait.assert_called_once_with(.2)


if __name__=='__main__':unittest.main()
