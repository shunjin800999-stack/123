"""Per-target pipeline using sanitized737 metadata and reproduced pixels.

No original screenshot bytes were present in the uploaded JSON. Windows
actions are simulated; optional integration checks use the actual OCR models.
"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image,ImageDraw,ImageFont

from backup_ocr import apply_target_header
from controls_probe import select_member_test
from member_batch import select_members_test
from member_row_scan import member_row_crops
from ocr_worker import infer_capture,runtime
from visual_members import verify_target_member_selection
from window_queue import ready_for_manual_invite

BASE=Path(__file__).parent
FIXTURE=BASE/'test_fixtures'/'target-only-737.json'
WINDOW={'hwnd':100,'pid':200,'path':r'C:\test\Telegram.exe'}
FONT=next(p for p in (Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    Path(r'C:\Windows\Fonts\segoeui.ttf')) if p.is_file())
HAS_MODEL=all(importlib.util.find_spec(n) for n in ('rapidocr','onnxruntime')) and (BASE/'ocr_models'/'manifest.json').is_file()


def frame(post=False,names=('737',)):
    r=json.loads(FIXTURE.read_text())['after' if post else 'before']
    for k in ('target_only_checkpoint','selection_header_ocr','visible_header_checkpoint','backup_ocr'):
        r.pop(k,None)
    if not post:
        r['regions']['list']['height']=76+56*(len(names)-1)
        r['ocr']['words']=[w for w in r['ocr']['words'] if w['top']<184]
        for i,name in enumerate(names):
            r['ocr']['words'].append({'text':name,'left':147,'top':231+112*i,'width':len(name)*14,'height':19})
        r['ocr']['text']='Add Members 5294 / 200000'
    return r


def pixels(r,*,post=False,empty=False,wrapped=False,selected=None):
    image=Image.new('RGB',(728,1160),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(str(FONT),20)
    labels=selected if selected is not None else ('735','736')+(() if not post or empty else ('737',))
    for i,name in enumerate(labels):
        x=16+158*i;y=108
        if wrapped and i==2:x=16;y=184
        tx=x+75;ty=y+19
        right=int(draw.textbbox((tx,ty),name,font=font)[2])+25
        draw.rounded_rectangle((x,y,right,y+64),radius=32,fill=(241,241,241))
        draw.ellipse((x,y,x+64,y+64),fill=(45,195,235) if i==0 else (238,83,139))
        # Complex old portrait and white avatar7 cannot affect this step.
        if i==0:draw.rectangle((x+12,y+4,x+57,y+59),fill=(30,30,30))
        else:draw.text((x+25,y+19),'7',font=font,fill='white')
        draw.text((tx,ty),name,font=font,fill=(30,30,30))
    return image


class TargetOnlyTests(unittest.TestCase):
    def replay(self,*,names=('737',),texts=('737',),scores=(1.,),mutate_before=None,
               mutate_after=None,empty_first=False,model=None,pause=False,ledger=('735','736')):
        native_calls=[];ocr_calls=[];posts=[];event=threading.Event()
        def native(window,script,payload):
            mode=payload.get('mode',script);native_calls.append((mode,copy.deepcopy(payload)))
            if mode=='click_once':
                self.assertIs(payload['target_member_only'],True)
                self.assertNotIn('selected_name_bounds',payload)
                return {'ok':True,'click_sent':True}
            post=mode=='inspect_member_visual.ps1'
            r=frame(post,names);r['image_path']=payload['image_path']
            if post:
                posts.append(r)
                if mutate_after:mutate_after(r)
            elif mutate_before:mutate_before(r)
            pixels(r,post=post,empty=empty_first and len(posts)==1).save(r['image_path'])
            return r
        def worker(r):
            def engine(crop,**flags):
                ocr_calls.append({'post':len(posts),'flags':flags,'shape':crop.shape})
                if r.get('header_scan_only'):
                    self.assertFalse(flags['use_det'])
                    self.assertLess(crop.shape[0],40);self.assertLess(crop.shape[1],110)
                    if model:return model(crop,**flags)
                    index=min(len(posts)-1,len(texts)-1)
                    return SimpleNamespace(txts=[texts[index]],scores=[scores[min(index,len(scores)-1)]])
                self.assertTrue(flags['use_det'])
                # All crops start in the member list. No header pixels reach OCR.
                row=sum(1 for c in ocr_calls if c['post']==0)
                bounds=member_row_crops(r,728,1160)[row-1]
                name=names[row-1];y=225+112*(row-1)
                self.assertEqual(crop.shape[:2],(bounds[3]-bounds[1],bounds[2]-bounds[0]))
                boxes=[[[142,y],[142+len(name)*16,y],[142+len(name)*16,y+30],[142,y+30]],
                    [[142,y+39],[356,y+39],[356,y+76],[142,y+76]]]
                return SimpleNamespace(txts=[name,'last seen recently'],scores=[1.,1.],
                    boxes=[np.array([[x-bounds[0],yy-bounds[1]] for x,yy in box]) for box in boxes])
            try:return infer_capture(r,engine,lambda _:'test',Image)
            finally:
                if pause and posts:event.set()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'step.png'
            with patch('controls_probe.run_window_script',side_effect=native),\
                 patch('backup_ocr.run_backup_ocr',side_effect=worker),\
                 patch('controls_probe.time.sleep') as wait,\
                 patch('ocr_worker.recognize_header_ink') as old_header:
                result=select_member_test(WINDOW,path,'737',expected_selected=ledger,fast_visible=True,
                    target_only=True,should_stop=event.is_set)
                old_header.assert_not_called()
            self.assertEqual(result,json.loads(path.with_suffix('.json').read_text()))
        return result,native_calls,ocr_calls,wait

    def test_737_ignores_old735736_and_finishes_first_read(self):
        r,native,calls,wait=self.replay()
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual([c[0] for c in native],['prepare','click_once','inspect_member_visual.ps1'])
        self.assertEqual(r['before']['backup_ocr']['selected_numbers'],[])
        self.assertEqual(r['after']['backup_ocr']['selected_numbers'],['737'])
        self.assertEqual(r['after']['target_only_checkpoint'],{'number':'737','stage':'after'})
        self.assertEqual(len(r['verification_reads']),1);self.assertNotIn('post_click_retry',r)
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False])
        wait.assert_called_once_with(.2)
        self.assertFalse(r['final_invite_clicked'])

    def test_second_row_is_read_only_after_first_mismatch(self):
        r,_,calls,_=self.replay(names=('E 737','737','737'))
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(r['before']['backup_ocr']['member_row_scan']['matched_row'],2)
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,True,False])

    def test_third_row_is_read_only_after_first_two_mismatches(self):
        r,_,calls,_=self.replay(names=('7370','E 737','737'))
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(r['before']['backup_ocr']['member_row_scan']['matched_row'],3)
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,True,True,False])

    def test_no_exact_row_never_clicks(self):
        r,native,calls,_=self.replay(names=('7370','E 737','737 bot'))
        self.assertFalse(r['selection_verified']);self.assertFalse(r['click_requested'])
        self.assertEqual([c[0] for c in native],['prepare']);self.assertEqual(len(calls),3)

    def test_unconfirmed_query_never_clicks(self):
        r,native,calls,_=self.replay(mutate_before=lambda r:r['search_preparation'].update(value_matches=False))
        self.assertFalse(r['click_requested']);self.assertEqual([c[0] for c in native],['prepare'])
        self.assertEqual(calls,[])

    def test_query_that_changes_during_capture_never_clicks(self):
        r,native,_,_=self.replay(mutate_before=lambda r:r['header_scroll'].update(search_value='738'))
        self.assertFalse(r['click_sent'])

    def test_first_missed_target_gets_one_capture_without_another_click(self):
        r,native,calls,wait=self.replay(texts=('736','737'),empty_first=True)
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual([c[0] for c in native],['prepare','click_once','inspect_member_visual.ps1','inspect_member_visual.ps1'])
        self.assertEqual(len(r['verification_reads']),2);self.assertTrue(r['post_click_retry']['verified'])
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False,False])
        wait.assert_called_once_with(.2)

    def test_both_missed_reads_stop_without_reclick(self):
        r,native,_,_=self.replay(texts=('736','736'))
        self.assertFalse(r['selection_verified']);self.assertEqual(len(r['verification_reads']),2)
        self.assertEqual(sum(c[0]=='click_once' for c in native),1)

    def test_below80_then80_recognition_uses_one_retry(self):
        r,_,_,_=self.replay(scores=(.79,.80),texts=('737','737'))
        self.assertTrue(r['selection_verified'],r['reason']);self.assertEqual(len(r['verification_reads']),2)

    def test_empty_recognition_gets_one_retry(self):
        r,_,_,_=self.replay(texts=('','737'))
        self.assertTrue(r['selection_verified'],r['reason']);self.assertEqual(len(r['verification_reads']),2)

    def test_known_target_is_not_clicked_again(self):
        r,native,calls,_=self.replay(ledger=('735','736','737'))
        self.assertFalse(r['selection_verified']);self.assertFalse(r['click_sent']);self.assertEqual(calls,[])

    def test_window_changed_after_click_does_not_retry(self):
        r,native,_,_=self.replay(mutate_after=lambda r:r.update(process_id=201))
        self.assertFalse(r['selection_verified']);self.assertEqual(len(r['verification_reads']),1)
        self.assertEqual(sum(c[0]=='click_once' for c in native),1)

    def test_pause_before_retry_retains_single_click(self):
        r,native,_,_=self.replay(texts=('736','737'),pause=True)
        self.assertEqual(r['state'],'paused');self.assertEqual(len(r['verification_reads']),1)
        self.assertEqual(sum(c[0]=='click_once' for c in native),1)

    def test_proof_from_another_frame_is_rejected(self):
        r,_,_,_=self.replay()
        after=copy.deepcopy(r['after']);after['backup_ocr']['image_sha256']='0'*64
        self.assertFalse(verify_target_member_selection(r['before'],after,'737',['735','736'])['selection_verified'])

    def test_query_digit_cannot_become_selected_evidence(self):
        r=frame(True);image=Image.new('RGB',(728,1160),'white');draw=ImageDraw.Draw(image)
        draw.rectangle((494,120,650,163),fill=(241,241,241))
        draw.text((495,127),'737',font=ImageFont.truetype(str(FONT),20),fill=(30,30,30))
        engine=SimpleNamespace()
        with tempfile.TemporaryDirectory() as folder:
            r['image_path']=str(Path(folder)/'frame.png');image.save(r['image_path'])
            with patch('backup_ocr.run_backup_ocr',side_effect=lambda r:infer_capture(r,engine,lambda _:'test',Image)):
                result=apply_target_header(r,'737','after')
        self.assertFalse(result['selection_header_ocr']['ok'])
        self.assertEqual(result['backup_ocr']['selected_numbers'],[])

    def test_native_guard_keeps_row_and_query_checks_but_skips_old_header_pixels(self):
        source=(BASE/'select_member.ps1').read_text(encoding='utf-8-sig')
        conditional=source.index('if ($payload.target_member_only -ne $true)')
        self.assertLess(source.index('[MemberInput]::SameRow($beforePath'),conditional)
        self.assertLess(source.index('Member search value changed. No click.'),conditional)
        self.assertGreater(source.index('[MemberInput]::SameHeader($beforePath'),conditional)
        self.assertGreater(source.index("$guardStage='live'"),conditional)


@unittest.skipUnless(HAS_MODEL,'Requires shipped OCR models')
class TargetOnlyRealModelTests(unittest.TestCase):
    replay=TargetOnlyTests.replay
    def test_real_models_read_only_new737_in_first_post_read(self):
        engine,_,_=runtime();r,native,calls,_=self.replay(model=engine)
        self.assertTrue(r['selection_verified'],r['reason'])
        self.assertEqual(r['after']['backup_ocr']['selected_numbers'],['737'])
        self.assertEqual(len(r['after']['backup_ocr']['header_crop_detections']),1)
        self.assertEqual(len(r['verification_reads']),1)
        self.assertEqual([c['flags']['use_det'] for c in calls],[True,False])

    def test_real_models_find_newest_chip_on_a_wrapped_row(self):
        r=frame(True);r['regions']['header']['height']=82
        r['regions']['header_ocr']['height']=82
        r['regions']['search'].update(left=837,top=317,width=265)
        r['header_scroll']['inner']['height']=82;r['header_scroll']['viewport']['height']=82
        image=pixels(r,post=True,wrapped=True);engine,version,Image=runtime()
        with tempfile.TemporaryDirectory() as folder:
            r['image_path']=str(Path(folder)/'frame.png');image.save(r['image_path'])
            with patch('backup_ocr.run_backup_ocr',side_effect=lambda r:infer_capture(r,engine,version,Image)):
                result=apply_target_header(r,'737','after')
        self.assertTrue(result['selection_header_ocr']['ok'],result['selection_header_ocr'])
        self.assertEqual(result['backup_ocr']['selected_numbers'],['737'])
        self.assertEqual(len(result['backup_ocr']['header_crop_detections']),1)

    def test_input_can_wrap_below_latest_chip_without_losing_target(self):
        r=frame(True);r['regions']['header']['height']=82;r['regions']['header_ocr']['height']=82
        r['regions']['search'].update(left=794,top=317,width=308)
        r['header_scroll']['inner']['height']=82;r['header_scroll']['viewport']['height']=82
        engine,version,Image=runtime()
        with tempfile.TemporaryDirectory() as folder:
            r['image_path']=str(Path(folder)/'frame.png');pixels(r,post=True).save(r['image_path'])
            with patch('backup_ocr.run_backup_ocr',side_effect=lambda r:infer_capture(r,engine,version,Image)):
                result=apply_target_header(r,'737','after')
        self.assertTrue(result['selection_header_ocr']['ok'],result['selection_header_ocr'])
        self.assertEqual(result['backup_ocr']['selected_numbers'],['737'])

    def test_complete_one_to_six_digit_labels_keep80_percent_threshold(self):
        engine,version,Image=runtime()
        for name in ('1','12','001','1000','123456'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as folder:
                r=frame(True);r['regions']['search'].update(left=1000,width=102)
                r['image_path']=str(Path(folder)/'frame.png');pixels(r,selected=[name]).save(r['image_path'])
                with patch('backup_ocr.run_backup_ocr',side_effect=lambda r:infer_capture(r,engine,version,Image)):
                    result=apply_target_header(r,name,'after')
                self.assertTrue(result['selection_header_ocr']['ok'],result['selection_header_ocr'])
                self.assertEqual(result['backup_ocr']['selected_numbers'],[name])
                self.assertGreaterEqual(result['backup_ocr']['accepted'][0]['score'],.80)


class TargetOnlyBatchTests(unittest.TestCase):
    def test_last_success_reuses_step_frame_and_records_all_successful_numbers(self):
        labels=['735','736','737'];calls=[];steps=[]
        base_step=TargetOnlyTests().replay()[0]
        def select(window,path,number,**kwargs):
            calls.append(number);self.assertTrue(kwargs['target_only']);self.assertTrue(kwargs['fast_visible'])
            self.assertEqual(list(kwargs['expected_selected']),labels[:len(calls)-1])
            step=copy.deepcopy(base_step)
            # This test exercises batch flow; exact per-step verification is
            # independently covered above and readiness validation below.
            step['number']=number;steps.append(step);return step
        initial=frame();initial['analysis']={'usable':True,'selected_numbers':[],'other_header_words':[]}
        with tempfile.TemporaryDirectory() as folder,\
             patch('member_batch.inspect_member_search',return_value=initial),\
             patch('backup_ocr.apply_visible_header',return_value=initial),\
             patch('visual_members.analyze_member_visual',return_value=initial['analysis']),\
             patch('member_batch.select_member_test',side_effect=select),\
             patch('member_batch.inspect_final_member') as final_scan:
            result=select_members_test(WINDOW,Path(folder)/'batch.json',labels,fast_visible=True,target_only=True)
        self.assertTrue(result['selection_verified'],result['reason']);self.assertEqual(calls,labels)
        self.assertEqual(result['selected_numbers'],labels);self.assertEqual(result['remaining_numbers'],[])
        self.assertEqual(result['final'],steps[-1]['after']);self.assertEqual(result['final_reads'],[])
        final_scan.assert_not_called();self.assertFalse(result['final_invite_clicked'])

    def test_readiness_requires_actual_target_evidence_not_just_success_flags(self):
        step=TargetOnlyTests().replay(ledger=())[0]
        result={'ok':True,'scope':'multi_member_selection','selection_verified':True,
            'state':'waiting_for_manual_invite','final_invite_clicked':False,'database_updated':False,
            'numbers':['737'],'selected_numbers':['737'],'remaining_numbers':[],
            'verification_mode':'target_only_each_step','initial':{'analysis':{'selected_numbers':[]}},
            'steps':[step],'final':copy.deepcopy(step['after'])}
        self.assertTrue(ready_for_manual_invite(result,WINDOW,['737']))
        for mutate in (lambda r:r['steps'][0].update(click_sent=False),
                       lambda r:r['steps'][0]['after']['backup_ocr'].update(image_sha256='0'*64),
                       lambda r:r.update(steps=[]),
                       lambda r:r['steps'][0].update(expected_selected=['736'])):
            changed=copy.deepcopy(result);mutate(changed)
            self.assertFalse(ready_for_manual_invite(changed,WINDOW,['737']))

    def test_gui_enables_target_only_for_both_group_buttons(self):
        import ast
        tree=ast.parse((BASE/'app.py').read_text(encoding='utf-8-sig'))
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and
               isinstance(n.func,ast.Name) and n.func.id=='select_pinned_member_queue']
        self.assertEqual(len(calls),1)
        keywords={k.arg:k.value for k in calls[0].keywords}
        self.assertTrue(keywords['target_only'].value);self.assertTrue(keywords['fast_visible'].value)

    def run_accounts(self,folder,*,bad_number=None,real_model=None):
        from pinned_members import PinnedMemberPlans,select_pinned_member_queue
        from store import Store
        from test_group_navigation import observation,opened
        records=[observation(1,'账号15'),observation(2,'账号16')]
        store=Store(Path(folder)/'db.sqlite3');plans=PinnedMemberPlans(store)
        for record,labels in zip(records,(['735','736','737'],['738','739','740'])):plans.save(record,labels)
        states={r['window']['hwnd']:[] for r in records};queries={};calls=[];ocr=[]
        path=Path(folder)/'accounts.json'
        def native(window,script,payload):
            hwnd=window['hwnd'];mode=payload.get('mode',script);calls.append((hwnd,mode,payload.get('number')))
            record=next(r for r in records if r['window']['hwnd']==hwnd)
            if mode=='click_once':
                states[hwnd].append(payload['number']);return {'ok':True,'click_sent':True}
            if mode=='prepare':queries[hwnd]=payload['number']
            number=queries[hwnd];post=mode=='inspect_member_visual.ps1'
            r=frame(post,(number,));r.update(window_handle=hwnd,process_id=window['pid'],
                dialog_runtime_id=opened(record,1)['dialog_runtime_id'],image_path=payload['image_path'])
            start=20+158*len(states[hwnd]) if states[hwnd] else 92
            r['regions']['search'].update(left=748+start/2,width=(708-start)/2)
            r['header_scroll']['search_value']='' if post else number
            if not post:r['search_preparation'].update(number=number)
            pixels(r,selected=states[hwnd]).save(r['image_path'])
            return r
        def worker(r):
            number=queries[r['window_handle']]
            def engine(crop,**flags):
                ocr.append((r['window_handle'],number,flags['use_det'],r.get('target_member_only',False)))
                if not flags['use_det']:
                    if number==bad_number:return SimpleNamespace(txts=['9999'],scores=[1.])
                    if real_model:return real_model(crop,**flags)
                    return SimpleNamespace(txts=[number],scores=[1.])
                offset=184 if r.get('target_member_only') else 0
                return SimpleNamespace(txts=[number,'last seen recently'],scores=[1.,1.],boxes=[
                    np.array([[142,225-offset],[194,225-offset],[194,255-offset],[142,255-offset]]),
                    np.array([[142,264-offset],[356,264-offset],[356,301-offset],[142,301-offset]])])
            return infer_capture(r,engine,lambda _:'test',Image)
        def navigation(records,slot,*args):
            if records[0]['account']=='账号16':
                durable=json.loads(path.read_text())
                self.assertEqual(durable['jobs'][0]['state'],'selection_finished')
                self.assertEqual(durable['jobs'][0]['selection']['selected_numbers'],['735','736','737'])
            return {'ok':True,'jobs':[{'navigation':opened(records[0],slot)}]}
        with patch('pinned_members.require_environment'),\
             patch('pinned_members.open_pinned_groups',side_effect=navigation),\
             patch('controls_probe.run_window_script',side_effect=native),\
             patch('backup_ocr.run_backup_ocr',side_effect=worker),\
             patch('controls_probe.time.sleep'),patch('member_batch.inspect_final_member') as final_scan:
            result=select_pinned_member_queue(plans.entries(records,1),1,path,fast_visible=True,target_only=True)
        final_scan.assert_not_called();plans.save_run(result)
        saved=plans.states('账号15')[1];store.close()
        return result,calls,ocr,saved

    def test_two_accounts_complete_and_preserve_per_target_records(self):
        with tempfile.TemporaryDirectory() as folder:r,calls,ocr,saved=self.run_accounts(folder)
        self.assertTrue(r['ok'],r['reason']);self.assertEqual(r['state'],'selection_finished')
        self.assertEqual([j['selection']['selected_numbers'] for j in r['jobs']],
                         [['735','736','737'],['738','739','740']])
        self.assertEqual(sum(mode=='click_once' for _,mode,_ in calls),6)
        self.assertEqual(sum(mode=='inspect_member_visual.ps1' for _,mode,_ in calls),6)
        self.assertEqual(saved['state'],'selection_finished');self.assertFalse(r['final_invite_clicked'])
        for job in r['jobs']:
            self.assertEqual(job['selection']['final_reads'],[])
            self.assertTrue(ready_for_manual_invite(job['selection'],job['window'],job['numbers']))

    def test_second_account_unconfirmed_target_preserves_first_and_does_not_count_failure(self):
        with tempfile.TemporaryDirectory() as folder:r,calls,ocr,saved=self.run_accounts(folder,bad_number='739')
        self.assertFalse(r['ok']);self.assertEqual([j['state'] for j in r['jobs']],['selection_finished','review'])
        second=r['jobs'][1]['selection']
        self.assertEqual(second['selected_numbers'],['738']);self.assertEqual(second['remaining_numbers'],['739','740'])
        self.assertEqual(saved['numbers'],['735','736','737'])
        self.assertEqual(sum(mode=='click_once' and number=='739' for _,mode,number in calls),1)
        self.assertFalse(any(number=='740' for _,_,number in calls))

    @unittest.skipUnless(HAS_MODEL,'Requires shipped OCR models')
    def test_real_models_confirm_each_new_chip_across_two_accounts(self):
        engine,_,_=runtime()
        with tempfile.TemporaryDirectory() as folder:r,calls,ocr,saved=self.run_accounts(folder,real_model=engine)
        self.assertTrue(r['ok'],r['reason'])
        self.assertEqual(sum(not det for _,_,det,_ in ocr),6)
        self.assertTrue(all(len(s['verification_reads'])==1 for j in r['jobs'] for s in j['selection']['steps']))


if __name__=='__main__':unittest.main()
