import json
import copy
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backup_ocr import inspect_backup_ocr, apply_selection_header, inspect_selection_visual, FRAME_KEYS
from ocr_worker import header_words
from visual_members import analyze_member_visual, plan_member_selection, member_header_guard
from controls_probe import select_member_test
from member_batch import select_members_test
import test_member_selection as single_tests
import test_visual_members as visual_tests


class Crop:
    width=18;height=26
    def __init__(self,color):self.color=color
    def convert(self,mode):return self
    def getdata(self):
        pixels=[self.color]*(self.width*self.height)
        if self.color==(241,241,241):
            for row in range(6,20):pixels[row*self.width+8]=(30,30,30)
        return pixels


class Image:
    width=720;height=1160;size=(720,1160)
    def crop(self,bounds):return Crop((120,200,80) if bounds[0]==120 else (241,241,241))


class BackupOcrTests(unittest.TestCase):
    def bound_report(self,selected=('1','2')):
        report=single_tests.live_report()
        report['regions']['search']['left']=740;report['regions']['search']['width']=210
        report['ocr']['words']=[visual_tests.VisualMemberTests().word('，',645,265),
            visual_tests.VisualMemberTests().word('0',670,265),visual_tests.VisualMemberTests().word('2',710,265)]
        backup=header_words(report,Image(),self.detections())
        # Use only the corresponding accepted evidence, never synthetic header words.
        backup['accepted']=[r for r in backup['accepted'] if r['text'] in selected]
        backup['selected_numbers']=list(selected)
        backup.update(ok=True,image_sha256='a'*64)
        report['backup_ocr']=backup
        report['selection_header_ocr']={'ok':True,'used_for_selection':True,'image_sha256':'a'*64,
            'frame':{key:copy.deepcopy(report[key]) for key in FRAME_KEYS}}
        return report

    def test_bound_backup_corrects_header_without_changing_list_evidence(self):
        r=self.bound_report();r['ocr']['words'].append(visual_tests.VisualMemberTests().word('3',675,320))
        a=analyze_member_visual(r,'3')
        self.assertTrue(a['usable']);self.assertEqual(a['selected_numbers'],['1','2'])
        self.assertEqual(a['other_header_words'],[]);self.assertEqual(a['list_matches'],1)
        self.assertEqual(plan_member_selection(r,'3',['1','2'])['action'],'click_once')
        r['ocr']['words'].append(visual_tests.VisualMemberTests().word('3',675,370))
        with self.assertRaises(ValueError):plan_member_selection(r,'3',['1','2'])

    def test_caret_margin_cannot_mask_a_selected_name_or_accept_unbound_evidence(self):
        r=self.bound_report(('1',));r['regions']['search']['left']=655;r['regions']['search']['width']=305
        r['selection_header_ocr']['frame']={key:copy.deepcopy(r[key]) for key in FRAME_KEYS}
        self.assertTrue(analyze_member_visual(r,'1')['usable'])
        with self.assertRaisesRegex(ValueError,'接触已选备注'):member_header_guard(r)
        r=self.bound_report();r['selection_header_ocr']['image_sha256']='b'*64
        with self.assertRaises(ValueError):member_header_guard(r)

    def test_caret_margin_retains_all_selected_label_protection_and_legacy_is_unchanged(self):
        r=self.bound_report();guard=member_header_guard(r)
        self.assertEqual(guard['selected_label_count'],2)
        self.assertEqual(len(guard['selected_name_bounds']),2)
        self.assertEqual(guard['search_caret_margin'],2)
        r.pop('selection_header_ocr')
        self.assertEqual(member_header_guard(r)['search_caret_margin'],0)

    def test_unbound_changed_frame_or_inconsistent_candidates_never_fall_back(self):
        for kind in ('hash','frame','summary','score','box','failed','ambiguous'):
            r=self.bound_report()
            if kind=='hash':r['selection_header_ocr']['image_sha256']='b'*64
            elif kind=='frame':r['dialog_runtime_id']='changed'
            elif kind=='summary':r['backup_ocr']['selected_numbers']=['2']
            elif kind=='score':r['backup_ocr']['accepted'][0]['score']=.79
            elif kind=='box':r['backup_ocr']['accepted'][0]['box']=[[0,0],[10,0],[10,10],[0,10]]
            elif kind=='failed':r['selection_header_ocr']['ok']=False
            else:r['backup_ocr']['rejected'].append({'pale_gray_border':.95,'dark_ink_fraction':.04,'reason':'置信度不足'})
            a=analyze_member_visual(r,'1')
            self.assertFalse(a['usable'],kind);self.assertEqual(a['selected_numbers'],[],kind)

    def test_worker_hash_must_match_existing_capture_and_capture_must_stay_unchanged(self):
        for kind in ('success','wrong_hash','changed_file','failed'):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'test.png';path.write_bytes(b'current capture')
                r=self.bound_report();r.pop('selection_header_ocr');r['image_path']=str(path)
                backup=r.pop('backup_ocr');backup['image_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
                def worker(report):
                    if kind=='wrong_hash':backup['image_sha256']='b'*64
                    if kind=='changed_file':path.write_bytes(b'replaced')
                    if kind=='failed':backup['ok']=False
                    return backup
                with patch('backup_ocr.run_backup_ocr',side_effect=worker):result=apply_selection_header(r)
                self.assertEqual(result['selection_header_ocr']['ok'],kind=='success')
                self.assertEqual(analyze_member_visual(result,'1')['usable'],kind=='success')

    def test_failed_header_before_click_stops_and_after_click_never_retries_input(self):
        for fail_at in ('before','after'):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'step.png';calls=[]
                def native(window,script,payload):
                    calls.append(payload.get('mode'))
                    if payload.get('mode')=='prepare':
                        Path(payload['image_path']).write_bytes(b'capture');return single_tests.live_report()
                    if payload.get('mode')=='check':
                        return {'ok':True,'mode':'check','read_only':True,'guard_stable':True,
                            'click_attempted':False,'click_sent':False,'final_invite_clicked':False,
                            'number':payload['number'],'before_sha256':payload['before_sha256']}
                    if payload.get('mode')=='click_once':return {'ok':True,'click_sent':True}
                    return single_tests.live_report(True)
                def header(r):
                    if (fail_at=='before' and len(calls)==1) or (fail_at=='after' and len(calls)>2):
                        r['selection_header_ocr']={'ok':False,'error':'OCR failed'}
                    return r
                with patch('controls_probe.run_window_script',side_effect=native),patch('controls_probe.time.sleep'):
                    result=select_member_test({},path,'1',header_reader=header)
                self.assertFalse(result['selection_verified']);self.assertFalse(result['final_invite_clicked'])
                self.assertEqual(calls.count('click_once'),0 if fail_at=='before' else 1)
                self.assertEqual(calls.count('prepare'),1)

    def test_existing_pair_passes_initial_and_final_checks_with_no_selection_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            def capture(window,path,number):
                r=self.bound_report();r['analysis']=analyze_member_visual(r,number);r['image_path']=str(path)
                return r
            with patch('member_batch.inspect_member_visual',side_effect=capture) as reads,\
                 patch('member_batch.select_member_test') as clicks,patch('member_batch.inspect_member_search') as search,\
                 patch('member_batch.time.sleep'):
                r=select_members_test({},Path(tmp)/'batch.json',['1','2'])
            self.assertTrue(r['selection_verified']);self.assertEqual(reads.call_count,6)
            clicks.assert_not_called();search.assert_not_called()
            self.assertEqual(r['state'],'waiting_for_manual_invite')
            self.assertFalse(r['database_updated']);self.assertFalse(r['final_invite_clicked'])

    def detections(self):
        box=lambda x:[[x,128],[x+18,128],[x+18,154],[x,154]]
        return [('1',0.99,box(90)),('2',0.99,box(120)),('2',0.99,box(155))]

    def test_green_avatar_number_is_excluded_and_black_label_numbers_retained(self):
        r=header_words(visual_tests.VisualMemberTests().report(),Image(),self.detections())
        self.assertEqual(r['selected_numbers'],['1','2']);self.assertEqual(len(r['rejected']),1)
        self.assertEqual(r['rejected'][0]['text'],'2')
        self.assertFalse(r['used_for_selection']);self.assertFalse(r['selection_verified'])

    def test_eighty_percent_boundary_is_accepted(self):
        detections=self.detections();detections[0]=('1',0.80,detections[0][2])
        r=header_words(visual_tests.VisualMemberTests().report(),Image(),detections)
        self.assertEqual(r['selected_numbers'],['1','2'])

    def test_reported_453_confidence_is_above_new_threshold(self):
        detections=self.detections();detections[0]=('1',0.85934,detections[0][2])
        r=header_words(visual_tests.VisualMemberTests().report(),Image(),detections)
        self.assertEqual(r['selected_numbers'],['1','2'])

    def test_low_confidence_is_retained_as_rejected_evidence(self):
        detections=self.detections();detections[0]=('1',0.79,detections[0][2])
        r=header_words(visual_tests.VisualMemberTests().report(),Image(),detections)
        self.assertEqual(r['selected_numbers'],['2'])
        self.assertEqual(r['rejected'][0]['reason'],'置信度不足')

    def test_bad_capture_dimensions_are_rejected(self):
        r=visual_tests.VisualMemberTests().report();r['capture']['width']=361
        with self.assertRaises(ValueError):header_words(r,Image(),self.detections())

    def test_search_digits_are_not_selected_labels(self):
        r=visual_tests.VisualMemberTests().report();r['regions']['search']['left']=640;r['regions']['search']['width']=310
        result=header_words(r,Image(),self.detections())
        self.assertEqual(result['selected_numbers'],[])

    def test_missing_optional_environment_does_not_capture_or_operate_window(self):
        with patch('backup_ocr.Path.is_file',return_value=False),patch('backup_ocr.inspect_member_visual') as capture:
            with self.assertRaises(ValueError):inspect_backup_ocr({},'test.png')
        capture.assert_not_called()

    def test_diagnostic_subprocess_and_failure_preserve_primary_report_without_selection(self):
        for output,code in ((b'{"ok":true,"selected_numbers":["1","2"],"used_for_selection":false}',0),
                            (b'not json',1)):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'test.png';primary=visual_tests.VisualMemberTests().report()
                primary['image_path']=str(path);primary['final_invite_clicked']=False
                completed=subprocess.CompletedProcess([],code,stdout=output,stderr=b'')
                with patch('backup_ocr.Path.is_file',return_value=True),\
                     patch('backup_ocr.inspect_member_visual',return_value=primary) as capture,\
                     patch('backup_ocr.run_backup_ocr',return_value=json.loads(output) if code==0 else {'ok':False,'error':'invalid json'}) as run:
                    r=inspect_backup_ocr({},path)
                capture.assert_called_once();run.assert_called_once()
                self.assertTrue(r['read_only']);self.assertFalse(r['final_invite_clicked'])
                self.assertEqual(run.call_args.args[0]['image_path'],str(path))
                saved=json.loads(path.with_suffix('.json').read_text())
                self.assertIn('backup_ocr',saved)
                self.assertEqual(saved['backup_ocr']['ok'],code==0)


if __name__=='__main__':unittest.main()
