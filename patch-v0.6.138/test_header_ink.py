import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from ocr_worker import header_words, recognize_header_ink, merge_header_evidence, infer_capture
from visual_members import selection_header_words, rect
from backup_ocr import FRAME_KEYS


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('PIL','numpy','cv2')),
                     'Requires the optional OCR environment')
class HeaderInkTests(unittest.TestCase):
    def scene(self,labels=('7',),search=True):
        from PIL import Image,ImageDraw
        # Each label has separate glyphs; no OCR detector output is supplied.
        image=Image.new('RGB',(400,160),'white');draw=ImageDraw.Draw(image)
        for i,label in enumerate(labels):
            x=10+i*80
            draw.rounded_rectangle((x,30,x+68,90),radius=20,fill=(241,241,241))
            draw.ellipse((x,30,x+38,90),fill=(70,160,240))
            # White avatar digits must never become a label candidate.
            draw.rectangle((x+15,48,x+20,70),fill='white')
            for j,_ in enumerate(label):
                draw.rectangle((x+44+j*9,49,x+48+j*9,69),fill=(40,40,40))
        if search:draw.rectangle((320,49,325,69),fill=(40,40,40))
        report={'capture':{'left':0,'top':0,'width':200,'height':80},
                'scale':2,'regions':{'header':{'left':0,'top':15,'width':200,'height':30},
                'search':{'left':155,'top':15,'width':45,'height':30}}}
        return report,image

    def engine(self,texts,scores=None):
        calls=[];texts=iter(texts);scores=iter(scores or [1.0]*20)
        def recognize(crop,**kwargs):
            self.assertEqual(kwargs,{'use_det':False,'use_cls':False,'use_rec':True})
            calls.append(crop.shape)
            text=next(texts)
            return SimpleNamespace(txts=None if text is None else (text,),scores=(next(scores),))
        return recognize,calls

    def test_single_small_label_is_read_without_avatar_or_search(self):
        report,image=self.scene();engine,calls=self.engine(['7'])
        extra,diagnostic=recognize_header_ink(report,image,engine)
        result=header_words(report,image,extra)
        self.assertEqual(result['selected_numbers'],['7']);self.assertEqual(len(calls),1)
        self.assertEqual(diagnostic[0]['source'],'gray_label_ink_crop')
        self.assertFalse(result['selection_verified']);self.assertFalse(result['final_invite_clicked'])

    def test_multiple_digits_are_grouped_and_leading_zeros_remain_exact(self):
        for label in ('12','001'):
            report,image=self.scene((label,));engine,calls=self.engine([label])
            extra,_=recognize_header_ink(report,image,engine)
            self.assertEqual(header_words(report,image,extra)['selected_numbers'],[label])
            self.assertEqual(len(calls),1)

    def test_separate_chips_are_not_merged_or_counted_twice(self):
        report,image=self.scene(('7','8'));engine,calls=self.engine(['7','8'])
        extra,_=recognize_header_ink(report,image,engine)
        result=header_words(report,image,extra)
        self.assertEqual(result['selected_numbers'],['7','8']);self.assertEqual(len(calls),2)
        merged=merge_header_evidence(result,result)
        self.assertEqual(merged['selected_numbers'],['7','8'])

    def test_search_only_is_empty_and_wrong_dimensions_fail(self):
        report,image=self.scene((),search=True);engine,calls=self.engine([])
        extra,_=recognize_header_ink(report,image,engine)
        self.assertEqual(extra,[]);self.assertEqual(calls,[])
        report['scale']=1
        with self.assertRaises(ValueError):recognize_header_ink(report,image,engine)

    def test_glyph_halo_uses_bounded_padding_without_lowering_background_threshold(self):
        from PIL import ImageDraw
        report,image=self.scene();draw=ImageDraw.Draw(image)
        draw.rectangle((51,46,61,72),fill=(205,205,205))
        draw.rectangle((54,49,58,69),fill=(40,40,40))
        engine,calls=self.engine(['9'])
        extra,diagnostics=recognize_header_ink(report,image,engine)
        summary=header_words(report,image,extra)
        self.assertEqual(summary['selected_numbers'],['9']);self.assertEqual(len(calls),1)
        self.assertEqual(diagnostics[0]['padding'],5)
        self.assertGreaterEqual(summary['accepted'][0]['pale_gray_border'],.8)

    def test_padding_limit_cannot_accept_a_large_non_gray_background(self):
        from PIL import ImageDraw
        report,image=self.scene();draw=ImageDraw.Draw(image)
        draw.rectangle((47,42,65,76),fill=(205,205,205))
        draw.rectangle((54,49,58,69),fill=(40,40,40))
        engine,calls=self.engine([])
        extra,_=recognize_header_ink(report,image,engine)
        self.assertEqual(extra,[]);self.assertEqual(calls,[])

    def test_recognition_disagreement_or_missing_text_stops(self):
        report,image=self.scene();engine,_=self.engine(['7'])
        extra,_=recognize_header_ink(report,image,engine)
        result=header_words(report,image,extra);changed=copy.deepcopy(result)
        changed['accepted'][0]['text']='1'
        with self.assertRaisesRegex(ValueError,'不一致'):merge_header_evidence(result,changed)
        engine,_=self.engine([None])
        with self.assertRaisesRegex(ValueError,'未识别文字'):recognize_header_ink(report,image,engine)

    def test_scroll_page_cut_glyph_is_deferred_to_overlap_without_guessing(self):
        report,image=self.scene();report['header_page']=True
        report['regions']['header_ocr']={'left':0,'top':25,'width':200,'height':20}
        # A detector reading a cut 7 as 1 does not become a current selected label.
        partial=('1',.999,[[51,48],[61,48],[61,72],[51,72]])
        result=header_words(report,image,[partial])
        self.assertEqual(result['selected_numbers'],[]);self.assertEqual(result['rejected'],[])
        engine,calls=self.engine([])
        extra,_=recognize_header_ink(report,image,engine)
        self.assertEqual(extra,[]);self.assertEqual(calls,[])
        # In an overlapping full view, normal high-confidence background checks apply.
        report['regions'].pop('header_ocr')
        engine,calls=self.engine(['7'])
        extra,_=recognize_header_ink(report,image,engine)
        self.assertEqual(header_words(report,image,extra)['selected_numbers'],['7'])

    def test_low_confidence_remains_rejected_and_nonnumeric_text_remains_visible(self):
        report,image=self.scene();engine,_=self.engine(['7'],[.79])
        extra,_=recognize_header_ink(report,image,engine)
        result=header_words(report,image,extra)
        self.assertEqual(result['selected_numbers'],[])
        self.assertEqual(result['rejected'][0]['reason'],'置信度不足')
        report.update(window_handle=1,process_id=2,dialog_runtime_id='dialog')
        result.update(ok=True,image_sha256='a'*64)
        report['backup_ocr']=result
        report['selection_header_ocr']={'ok':True,'used_for_selection':True,'image_sha256':'a'*64,
            'frame':{key:copy.deepcopy(report[key]) for key in FRAME_KEYS}}
        with self.assertRaises(ValueError):
            selection_header_words(report,rect(report['capture']),
                {key:rect(report['regions'][key]) for key in ('header','search')},2)
        engine,_=self.engine(['O'])
        extra,_=recognize_header_ink(report,image,engine)
        result=header_words(report,image,extra)
        self.assertEqual(result['selected_numbers'],[]);self.assertEqual(result['other_header_words'],['O'])

    def shared_engine(self,initial_detection=True,initial_recognition=True):
        # RapidOCR retains these flags. Recognition-only results have no boxes.
        import numpy as np
        class Engine:
            def __init__(self):
                self.use_det=initial_detection;self.use_rec=initial_recognition;self.use_cls=True;self.calls=[]
            def __call__(self,image,**flags):
                for key,value in flags.items():
                    if value is not None:setattr(self,key,value)
                if self.use_cls:raise AssertionError('Unexpected orientation classification')
                if not self.use_rec:raise AssertionError('Recognition mode was not restored')
                self.calls.append('frame' if self.use_det else 'crop')
                if self.use_det:
                    return SimpleNamespace(boxes=np.empty((0,4,2)),txts=(),scores=())
                return SimpleNamespace(txts=('7',),scores=(1.0,))
        return Engine()

    def test_five_frames_restore_detection_after_each_recognition_only_crop(self):
        from PIL import Image
        report,image=self.scene();engine=self.shared_engine()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'frame.png';image.save(path)
            report.update(ok=True,read_only=True,scope='member_visual',image_path=str(path))
            outputs=[infer_capture(dict(report,dialog_runtime_id='frame-'+str(i)),engine,lambda _: 'test',Image) for i in range(5)]
        self.assertEqual([r['selected_numbers'] for r in outputs],[['7']]*5)
        self.assertEqual(engine.calls,['frame','crop']*5)
        self.assertTrue(all(r['ok'] and not r['final_invite_clicked'] for r in outputs))

    def test_full_frame_restores_previously_disabled_detection_and_recognition(self):
        from PIL import Image
        report,image=self.scene();engine=self.shared_engine(False,False)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'frame.png';image.save(path)
            report.update(ok=True,read_only=True,scope='member_visual',image_path=str(path))
            result=infer_capture(report,engine,lambda _: 'test',Image)
        self.assertEqual(result['selected_numbers'],['7'])
        self.assertEqual(engine.calls,['frame','crop'])


if __name__=='__main__':unittest.main()
