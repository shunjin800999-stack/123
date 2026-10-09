import copy
import unittest
from visual_members import avatar_crop_fragment

class AvatarCropTests(unittest.TestCase):
    def setUp(self):
        self.word={'text':'2','score':.4208,'box':[[39,187],[62,187],[62,209],[39,209]],'reason':'置信度不足'}
        self.backup={'accepted':[{'text':'386','score':.99999,'box':[[85,204],[139,204],[139,236],[85,236]]}],
                     'header_crop_detections':[dict(self.word,source='gray_label_ink_crop')]}
    def test_reported_avatar_crop_is_excluded(self):
        self.assertTrue(avatar_crop_fragment(self.word,self.backup))
    def test_missing_crop_provenance_and_unproven_chip_rejected(self):
        for key in ('header_crop_detections','accepted'):
            b=copy.deepcopy(self.backup);b[key]=[]
            self.assertFalse(avatar_crop_fragment(self.word,b))
    def test_label_area_and_other_row_are_not_excluded(self):
        for box in ([[85,204],[100,204],[100,225],[85,225]],[[39,300],[62,300],[62,322],[39,322]]):
            w=dict(self.word,box=box);b=copy.deepcopy(self.backup);b['header_crop_detections']=[dict(w,source='gray_label_ink_crop')]
            self.assertFalse(avatar_crop_fragment(w,b))
    def test_regular_ocr_and_other_rejection_reasons_remain_blocking(self):
        b=copy.deepcopy(self.backup);b['header_crop_detections'][0]['source']='ordinary'
        self.assertFalse(avatar_crop_fragment(self.word,b))
        self.assertFalse(avatar_crop_fragment(dict(self.word,reason='识别框过小'),self.backup))
