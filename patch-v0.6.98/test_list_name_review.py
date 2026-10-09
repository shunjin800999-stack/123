import copy
import unittest
from types import SimpleNamespace
from PIL import Image
from ocr_worker import review_list_names
from test_rapid_list import portuguese_sample,detection,bind
from visual_members import analyze_member_visual,plan_member_selection

class ListReviewTests(unittest.TestCase):
    def report(self,reads=None):
        r=portuguese_sample();r['backup_ocr']['detections'][0].update(text='44',score=.88298)
        image=Image.new('RGB',(728,1160),'white')
        outputs=iter(reads or [('44',.999),('44',.999)])
        def engine(crop,**flags):
            self.assertFalse(flags['use_det']);self.assertTrue(flags['use_rec'])
            text,score=next(outputs);return SimpleNamespace(txts=(text,),scores=(score,))
        detections=[(d['text'],d['score'],d['box']) for d in r['backup_ocr']['detections']]
        r['backup_ocr']['list_name_reviews']=review_list_names(r,image,engine,detections,'a'*64)
        return r
    def test_two_crop_reads_confirm_without_changing_raw_score(self):
        r=self.report();a=analyze_member_visual(r,'44')
        self.assertTrue(a['usable'],a['reason']);self.assertEqual(a['list_matches'],1)
        self.assertEqual(plan_member_selection(r,'44')['action'],'click_once')
        self.assertEqual(r['backup_ocr']['detections'][0]['score'],.88298)
    def test_native_wider_crop_score_still_requires_second_independent_success(self):
        good=self.report([('44',.99839),('44',.999)])
        self.assertTrue(analyze_member_visual(good,'44')['usable'])
        self.assertEqual(len(good['backup_ocr']['list_name_reviews'][0]['reads']),2)
        bad=self.report([('44',.99839),('44',.88442)])
        self.assertFalse(analyze_member_visual(bad,'44')['usable'])

    def test_low_score_wrong_text_or_disagreeing_reads_block(self):
        for reads in ([('44',.89),('44',1)],[('44',1),('49',1)],[('49',1),('49',1)]):
            self.assertFalse(analyze_member_visual(self.report(reads),'44')['usable'])
    def test_wrong_hash_box_crop_or_missing_read_blocks(self):
        for kind in ('hash','box','crop','count'):
            r=self.report();v=r['backup_ocr']['list_name_reviews'][0]
            if kind=='hash':v['image_sha256']='b'*64
            if kind=='box':v['box']=copy.deepcopy(v['box']);v['box'][0][0]-=1
            if kind=='crop':v['reads'][0]['crop_bounds'][0]-=1
            if kind=='count':v['reads'].pop()
            self.assertFalse(analyze_member_visual(r,'44')['usable'],kind)
    def test_status_alignment_is_required_even_with_good_crop_reads(self):
        r=self.report();r['backup_ocr']['detections'][1]['box']=[[500,266],[600,266],[600,296],[500,296]]
        self.assertEqual(analyze_member_visual(r,'44')['list_matches'],0)
    def test_duplicate_crops_cannot_override_two_contact_rows(self):
        r=self.report();first=r['backup_ocr']['detections'][0]
        second=copy.deepcopy(first);second['box']=[[x,y+76] for x,y in first['box']]
        r['backup_ocr']['detections'] +=[second,detection('visto em 2026/9/27',(142,342,377,372))]
        r['regions']['list']['height']=200
        v=copy.deepcopy(r['backup_ocr']['list_name_reviews'][0]);v['detection_index']=2;v['box']=second['box']
        for read in v['reads']:read['crop_bounds'][1]+=76;read['crop_bounds'][3]+=76
        r['backup_ocr']['list_name_reviews'].append(v)
        bind(r)
        self.assertEqual(analyze_member_visual(r,'44')['list_matches'],2)
        with self.assertRaises(ValueError):plan_member_selection(r,'44')
