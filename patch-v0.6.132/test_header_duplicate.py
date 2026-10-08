import copy,unittest
from visual_members import corroborated_header_duplicate
class HeaderDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.word={'text':'408','score':.94767,'box':[[85,123],[140,123],[140,156],[85,156]],'reason':'置信度不足'}
        self.accepted={'text':'408','score':1.,'box':[[88,127],[136,127],[136,154],[88,154]]}
        self.backup={'accepted':[self.accepted],'detections':[self.word],
                     'header_crop_detections':[dict(self.accepted,source='gray_label_ink_crop')]}
    def test_reported_duplicate_supported_by_bound_crop(self):
        self.assertTrue(corroborated_header_duplicate(self.word,self.backup))
    def test_different_label_or_location_not_ignored(self):
        for word in [dict(self.word,text='409'),dict(self.word,box=[[300,123],[355,123],[355,156],[300,156]])]:
            b=copy.deepcopy(self.backup);b['detections']=[word]
            self.assertFalse(corroborated_header_duplicate(word,b))
    def test_missing_provenance_or_low_confidence_crop_not_ignored(self):
        for key in ('header_crop_detections','detections','accepted'):
            b=copy.deepcopy(self.backup);b[key]=[];self.assertFalse(corroborated_header_duplicate(self.word,b))
        b=copy.deepcopy(self.backup);b['accepted'][0]['score']=.79
        self.assertFalse(corroborated_header_duplicate(self.word,b))
