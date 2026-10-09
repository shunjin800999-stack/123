"""Actual crop pipeline: lower rows reach OCR only after preceding failures."""
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
from controls_probe import select_member_test,WindowActionError
from member_row_scan import member_row_crops,first_member_strip,matched_member_area
from ocr_worker import infer_capture
from test_first_matching_member import evidence,rows,WINDOW
from visual_members import analyze_member_visual,plan_member_selection


class ProgressiveOCRTests(unittest.TestCase):
    def scene(self,names,*,scores=None,missing=None):
        report=rows(names)
        for key in ('selection_header_ocr','visible_header_checkpoint'):
            report.pop(key,None)
        report['first_row_scan']=True
        report['ocr_scan_bounds']=first_member_strip(report,728,1160)
        # Windows OCR's list input contains the first row only.
        report['ocr']['words']=[w for w in report['ocr']['words']
            if w['top']<report['ocr_scan_bounds'][3]]
        report['ocr']['text']='Add Members Cancel Add'
        scores=list(scores or [1.]*len(names));missing=set(missing or ())
        calls=[]
        def engine_for(r):
            sequence=iter(range(1,len(names)+1))
            def engine(crop,**flags):
                self.assertEqual(flags,{'use_det':True,'use_cls':False,'use_rec':True})
                self.assertIsInstance(crop,np.ndarray)
                if r.get('header_scan_only'):
                    from ocr_worker import header_crop_bounds
                    bounds=list(header_crop_bounds(r,728,1160));index=0
                else:
                    index=next(sequence)
                    bounds=first_member_strip(r,728,1160) if index==1 else member_row_crops(r,728,1160)[index-1]
                self.assertEqual(crop.shape,(bounds[3]-bounds[1],bounds[2]-bounds[0],3))
                calls.append({'row':index,'bounds':bounds,'pixels':crop.copy()})
                detections=[]
                if index in (0,1):
                    detections=[(w['text'],w['score'],w['box']) for w in r['backup_ocr']['accepted']]
                if index:
                    y=304+112*(index-1)
                    if index not in missing:
                        detections.append((names[index-1],scores[index-1],
                            [[143,y],[143+16*len(names[index-1]),y],[143+16*len(names[index-1]),y+32],[143,y+32]]))
                    detections.append(('last seen recently',1.,
                        [[142,y+43],[430,y+43],[430,y+75],[142,y+75]]))
                return SimpleNamespace(txts=[d[0] for d in detections],scores=[d[1] for d in detections],
                    boxes=[np.array([[x-bounds[0],y-bounds[1]] for x,y in d[2]]) for d in detections])
            return engine
        def write_image(r,path):
            image=Image.new('RGB',(728,1160),'white');draw=ImageDraw.Draw(image)
            for word in r['backup_ocr']['accepted']:
                xs=[p[0] for p in word['box']];ys=[p[1] for p in word['box']]
                l,t,rr,b=min(xs),min(ys),max(xs),max(ys)
                draw.rectangle((l,t,rr,b),fill=(241,241,241))
                draw.rectangle((l+6,t+6,l+12,b-6),fill=(40,40,40))
            # Mark row two's body. This pixel must be absent from the first crop.
            crops=member_row_crops(r,728,1160)
            if len(crops)>1:draw.rectangle((20,crops[1][1]+10,30,crops[1][1]+20),fill=(200,20,20))
            image.save(path)
        return report,calls,engine_for,write_image

    def recognize(self,names,**kwargs):
        r,calls,engine_for,write=self.scene(names,**kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'frame.png';write(r,path);r['image_path']=str(path)
            def infer(request):
                return infer_capture(request,engine_for(request),lambda _: 'offline-test',Image)
            with patch('backup_ocr.run_backup_ocr',side_effect=infer),\
                 patch('ocr_worker.recognize_header_ink',return_value=([],[])),\
                 patch('ocr_worker.review_list_names',return_value=[]):
                result=apply_visible_header(r,evidence()['expected_selected'],'1001','before')
        return result,calls

    def test_first_match_ends_ocr_before_second_or_third_rows(self):
        result,calls=self.recognize(('1001','1001','1001'))
        self.assertTrue(result['selection_header_ocr']['ok'],result['selection_header_ocr'])
        self.assertEqual([c['row'] for c in calls],[1])
        self.assertEqual(calls[0]['bounds'],[0,0,728,416])
        self.assertFalse(np.any(np.all(calls[0]['pixels']==(200,20,20),axis=2)))
        self.assertEqual(result['backup_ocr']['member_row_scan']['matched_row'],1)
        self.assertEqual(len(result['backup_ocr']['member_row_scan']['scans']),1)
        self.assertEqual(plan_member_selection(result,'1001',evidence()['expected_selected'])['y'],378)

    def test_mismatched_first_row_reads_second_and_stops_before_third(self):
        result,calls=self.recognize(('E 1001','1001','1001'))
        self.assertEqual([c['row'] for c in calls],[1,2])
        self.assertEqual(result['backup_ocr']['member_row_scan']['matched_row'],2)
        self.assertEqual(plan_member_selection(result,'1001',evidence()['expected_selected'])['y'],434)

    def test_mismatched_first_and_second_rows_read_third_only_after_failures(self):
        result,calls=self.recognize(('10010','1001MACAU','1001','1001'))
        self.assertEqual([c['row'] for c in calls],[1,2,3])
        scans=result['backup_ocr']['member_row_scan']['scans']
        self.assertEqual([s['matched'] for s in scans],[False,False,True])
        self.assertEqual(plan_member_selection(result,'1001',evidence()['expected_selected'])['y'],490)

    def test_unrecognized_first_row_falls_back_when_windows_also_has_no_match(self):
        result,calls=self.recognize(('Other','1001','1001'),missing=(1,))
        self.assertEqual([c['row'] for c in calls],[1,2])
        self.assertEqual(result['backup_ocr']['member_row_scan']['matched_row'],2)

    def test_low_confidence_first_failure_can_use_a_good_second_row(self):
        result,calls=self.recognize(('1001','1001','1001'),scores=(.79,1.,1.))
        self.assertEqual([c['row'] for c in calls],[1,2])
        self.assertFalse(result['backup_ocr']['member_row_scan']['scans'][0]['matched'])
        # The first Windows observation must not override the chosen second row.
        self.assertEqual(plan_member_selection(result,'1001',evidence()['expected_selected'])['y'],434)

    def test_no_match_records_failures_without_a_candidate(self):
        result,calls=self.recognize(('E 1001','10010','1001MACAU'))
        self.assertEqual([c['row'] for c in calls],[1,2,3])
        self.assertIsNone(result['backup_ocr']['member_row_scan']['matched_row'])
        self.assertEqual(analyze_member_visual(result,'1001')['list_matches'],0)
        with self.assertRaises(ValueError):plan_member_selection(result,'1001',evidence()['expected_selected'])

    def test_wrong_windows_crop_never_reaches_the_ocr_engine(self):
        r,calls,engine_for,write=self.scene(('1001','1001'))
        r['ocr_scan_bounds'][3]+=112
        r.update(progressive_member_ocr=True,member_row_target='1001')
        with tempfile.TemporaryDirectory() as tmp:
            r['image_path']=str(Path(tmp)/'frame.png');write(r,r['image_path'])
            with self.assertRaisesRegex(ValueError,'Windows识别范围'):
                infer_capture(r,engine_for(r),lambda _: 'test',Image)
        self.assertEqual(calls,[])

    def test_bad_matched_row_scope_or_target_is_rejected(self):
        good,_=self.recognize(('E 1001','1001','1001'))
        for key,value in [('matched_row',1),('crop_bounds',[0,0,728,1160]),('number','1002')]:
            with self.subTest(key=key):
                r=copy.deepcopy(good);r['backup_ocr']['member_row_scan'][key]=value
                self.assertFalse(analyze_member_visual(r,'1001')['usable'])

    def test_geometry_follows_search_ui_scale_and_excludes_offscreen_rows(self):
        r=rows(('1001',)*7)
        crops=member_row_crops(r,728,1160)
        self.assertEqual(crops[0],[0,264,728,416])
        self.assertEqual(len(crops),7)
        self.assertLessEqual(crops[-1][3],1052)
        r['regions']['search']['height']=50
        self.assertEqual(first_member_strip(r,728,1160),[0,0,728,568])
        r['regions']['list']['top']-=20
        with self.assertRaises(ValueError):member_row_crops(r,728,1160)

    def test_postclick_header_reads_do_not_recognize_list_rows(self):
        r,calls,engine_for,write=self.scene(('1001','1001'))
        r['backup_ocr']['accepted'].append(dict(r['backup_ocr']['accepted'][-1],text='1001',
            box=[[400,203],[470,203],[470,236],[400,236]]))
        r['regions']['search'].update(left=1008,width=54)
        with tempfile.TemporaryDirectory() as tmp:
            r['image_path']=str(Path(tmp)/'frame.png');write(r,r['image_path'])
            with patch('backup_ocr.run_backup_ocr',side_effect=lambda request:infer_capture(request,engine_for(request),lambda _:'test',Image)),\
                 patch('ocr_worker.recognize_header_ink',return_value=([],[])):
                result=apply_visible_header(r,evidence()['expected_selected'],'1001','after')
        self.assertTrue(result['selection_header_ocr']['ok'])
        self.assertEqual([c['row'] for c in calls],[0])
        self.assertIn('1001',result['backup_ocr']['selected_numbers'])

    def test_real_crop_pipeline_sends_one_click_and_verifies_the_new_chip(self):
        for names,expected_row in [(('1001','1001','1001'),1),(('E 1001','1001','1001'),2)]:
            with self.subTest(expected_row=expected_row),tempfile.TemporaryDirectory() as tmp:
                initial,calls,engine_for,write=self.scene(names)
                native_calls=[];clicks=[]
                def native(window,script,payload):
                    mode=payload.get('mode',script);native_calls.append(mode)
                    if mode=='click_once':
                        clicks.append((payload['x'],payload['y']));return {'ok':True,'click_sent':True}
                    self.assertTrue(payload['first_row_scan'])
                    r=copy.deepcopy(initial)
                    if clicks:
                        self.assertTrue(payload['header_scan_only'])
                        r['regions']['search'].update(left=1008,width=54)
                        r['backup_ocr']['accepted'].append(dict(r['backup_ocr']['accepted'][-1],text='1001',
                            box=[[400,203],[470,203],[470,236],[400,236]]))
                        r['ocr']['words']=[w for w in r['ocr']['words'] if w['top']<264]
                        r['ocr_scan_bounds']=[0,0,728,264]
                    r['image_path']=payload['image_path'];write(r,r['image_path']);return r
                with patch('controls_probe.run_window_script',side_effect=native),\
                     patch('backup_ocr.run_backup_ocr',side_effect=lambda request:infer_capture(request,engine_for(request),lambda _:'test',Image)),\
                     patch('ocr_worker.recognize_header_ink',return_value=([],[])),\
                     patch('ocr_worker.review_list_names',return_value=[]),patch('controls_probe.time.sleep'):
                    result=select_member_test(WINDOW,Path(tmp)/'step.png','1001',
                        expected_selected=evidence()['expected_selected'],fast_visible=True)
                self.assertTrue(result['selection_verified'],result['reason'])
                self.assertEqual(native_calls,['prepare','click_once','inspect_member_visual.ps1'])
                self.assertEqual(clicks,[(835,378+56*(expected_row-1))])
                self.assertEqual([c['row'] for c in calls],list(range(1,expected_row+1))+[0])
                self.assertFalse(result['final_invite_clicked'])


if __name__=='__main__':unittest.main()
