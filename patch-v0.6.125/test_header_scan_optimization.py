import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from PIL import Image
import numpy as np
from ocr_worker import header_crop_bounds,infer_capture
class HeaderScanOptimizationTests(unittest.TestCase):
    def report(self,path):
        return {'ok':True,'read_only':True,'scope':'member_visual','image_path':str(path),
                'capture':{'left':100,'top':200,'width':300,'height':500},'scale':2,
                'regions':{'header':{'left':100,'top':240,'width':300,'height':80}},'header_scan_only':True}
    def test_crop_keeps_only_header_and_rebases_detections(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'image.png';Image.new('RGB',(600,1000),'white').save(p);r=self.report(p)
            def engine(image,**flags):
                self.assertEqual(image.shape,(160,600,3))
                return SimpleNamespace(txts=['408'],scores=[.90],boxes=[np.array([[10,10],[40,10],[40,30],[10,30]])])
            summary={'accepted':[],'rejected':[],'selected_numbers':[],'other_header_words':[]}
            with patch('ocr_worker.header_words',return_value=summary) as words,patch('ocr_worker.recognize_header_ink',return_value=([],[])),patch('ocr_worker.merge_header_evidence',return_value=summary),patch('ocr_worker.review_list_names') as list_reviews:
                result=infer_capture(r,engine,lambda _: 'test',Image)
                self.assertEqual(result['detections'][0]['box'],[[10.,90.],[40.,90.],[40.,110.],[10.,110.]])
                self.assertTrue(result['header_scan_only']);list_reviews.assert_not_called()
    def test_invalid_or_empty_crop_is_rejected(self):
        r=self.report('unused');r['scale']=0
        with self.assertRaises(ValueError):header_crop_bounds(r,600,1000)
        r=self.report('unused');r['regions']['header']['top']=900
        with self.assertRaises(ValueError):header_crop_bounds(r,600,1000)
