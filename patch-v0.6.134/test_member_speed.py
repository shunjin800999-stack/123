"""Target-row resize and native guard checks. Windows input is simulated."""
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image,ImageDraw,ImageFont

import test_premium_member_star as premium
import test_target_only_members as target
from controls_probe import select_member_test
from member_row_scan import member_row_crops
from ocr_worker import infer_capture,recognize_member_crop,runtime
from visual_members import numeric_member_label

BASE=Path(__file__).parent


class MemberResizeTests(unittest.TestCase):
    def test_target_resize_restores_cached_detector_after_success(self):
        detector=SimpleNamespace(limit_type='min');calls=[]
        def engine(pixels,**flags):
            calls.append((detector.limit_type,pixels.shape,flags));return 'recognized'
        engine.text_det=detector
        self.assertEqual(recognize_member_crop(engine,np.zeros((152,728,3)),target_only=True),'recognized')
        self.assertEqual(calls[0],('max',(152,728,3),{'use_det':True,'use_cls':False,'use_rec':True}))
        self.assertEqual(detector.limit_type,'min')

    def test_failed_row_restores_detector_before_another_ocr_request(self):
        detector=SimpleNamespace(limit_type='min')
        def engine(*args,**kwargs):raise ValueError('recognition failed')
        engine.text_det=detector
        with self.assertRaisesRegex(ValueError,'recognition failed'):
            recognize_member_crop(engine,np.zeros((152,728,3)),target_only=True)
        self.assertEqual(detector.limit_type,'min')

    def test_non_target_scans_keep_the_default_resize(self):
        detector=SimpleNamespace(limit_type='min');seen=[]
        def engine(*args,**kwargs):seen.append(detector.limit_type)
        engine.text_det=detector
        recognize_member_crop(engine,np.zeros((152,728,3)),target_only=False)
        self.assertEqual(seen,['min']);self.assertEqual(detector.limit_type,'min')

    def test_engine_without_detector_attribute_remains_supported(self):
        def engine(*args,**kwargs):return 'result'
        self.assertEqual(recognize_member_crop(engine,np.zeros((152,728,3)),target_only=True),'result')

    @unittest.skipUnless(target.HAS_MODEL,'Requires shipped OCR models')
    def test_real_model_bounds_the_first_row_tensor_without_changing_other_modes(self):
        engine,_,_=runtime();detector=engine.text_det;seen=[]
        original=detector.get_preprocess
        def preprocess(max_wh):
            operation=original(max_wh)
            def run(image):
                result=operation(image);seen.append((detector.limit_type,result.shape));return result
            return run
        row=premium.pixels(premium.native_frame()).crop((0,304,728,456))
        with patch.object(detector,'get_preprocess',side_effect=preprocess):
            recognized=recognize_member_crop(engine,np.asarray(row),target_only=True)
        self.assertEqual(seen,[('max',(1,3,160,736))])
        self.assertTrue(any(numeric_member_label(text)=='1027' for text in recognized.txts))
        self.assertEqual(detector.limit_type,'min')

    @unittest.skipUnless(target.HAS_MODEL,'Requires shipped OCR models')
    def test_real_model_reads_short_long_and_legacy_numbers_at_multiple_ui_sizes(self):
        engine,_,_=runtime()
        for number in ('1','12','001','735','1000','1027','12345','123456'):
            for unit in (.75,1.,1.5,2.):
                with self.subTest(number=number,ui_unit=unit):
                    image=Image.new('RGB',(728,152),'white');draw=ImageDraw.Draw(image)
                    font=ImageFont.truetype(str(target.FONT),24)
                    draw.text((149,34),number,font=font,fill=(25,25,25))
                    draw.text((149,78),'last seen recently',font=ImageFont.truetype(str(target.FONT),20),fill=(145,145,145))
                    image=image.resize((round(728*unit),round(152*unit)))
                    result=recognize_member_crop(engine,np.asarray(image),target_only=True)
                    matches=[score for text,score in zip(result.txts,result.scores) if numeric_member_label(text)==number]
                    self.assertTrue(matches,(number,unit,result.txts));self.assertGreaterEqual(max(matches),.8)
        self.assertEqual(engine.text_det.limit_type,'min')

    @unittest.skipUnless(target.HAS_MODEL,'Requires shipped OCR models')
    def test_real_optimized_pipeline_selects_once_and_finishes_first_verification(self):
        model,version,_=runtime()
        for number in ('735','1000','1027','123456'):
            with self.subTest(number=number),tempfile.TemporaryDirectory() as folder:
                native_calls=[];ocr_calls=[]
                def native(window,script,payload):
                    mode=payload.get('mode',script);native_calls.append(mode)
                    if mode=='click_once':return {'ok':True,'click_attempted':True,'click_sent':True,'list_height_reused':True}
                    post=mode=='inspect_member_visual.ps1';r=premium.native_frame()
                    r['search_preparation']['number']=number;r['header_scroll']['search_value']='' if post else number
                    for word in r['ocr']['words']:
                        if word['text']=='1027':word.update(text=number,width=len(number)*15)
                    if post:r['regions']['search'].update(left=960,width=102)
                    r['image_path']=payload['image_path']
                    image=premium.pixels(r,post=post,label=number,star=False)
                    if not post:
                        draw=ImageDraw.Draw(image);font=ImageFont.truetype(str(target.FONT),24)
                        center=149+draw.textlength(number,font=font)+14
                        points=[(center+(12 if i%2==0 else 5)*math.cos(-math.pi/2+i*math.pi/5),
                                 353+(12 if i%2==0 else 5)*math.sin(-math.pi/2+i*math.pi/5)) for i in range(10)]
                        draw.polygon(points,fill=(48,174,225))
                    image.save(r['image_path']);return r
                def worker(r):
                    def engine(pixels,**flags):
                        ocr_calls.append((flags['use_det'],model.text_det.limit_type));return model(pixels,**flags)
                    engine.text_det=model.text_det
                    return infer_capture(r,engine,version,Image)
                with patch('controls_probe.run_window_script',side_effect=native),\
                     patch('backup_ocr.run_backup_ocr',side_effect=worker),\
                     patch('controls_probe.time.sleep') as wait:
                    result=select_member_test(target.WINDOW,Path(folder)/'step.png',number,
                        expected_selected=[],fast_visible=True,target_only=True)
                self.assertTrue(result['selection_verified'],result['reason'])
                self.assertEqual(native_calls,['prepare','click_once','inspect_member_visual.ps1'])
                self.assertEqual(ocr_calls,[(True,'max'),(False,'min')])
                self.assertEqual(len(result['verification_reads']),1);wait.assert_called_once_with(.2)
                self.assertEqual(result['before']['backup_ocr']['member_row_scan']['detector_resize'],'bounded_target_row')
                self.assertFalse(result['final_invite_clicked']);self.assertEqual(model.text_det.limit_type,'min')


def powershell_path():
    return shutil.which('pwsh') or shutil.which('powershell') or (
        '/tmp/powershell-7.4.6/pwsh' if Path('/tmp/powershell-7.4.6/pwsh').is_file() else None)


class MemberNativeSpeedTests(unittest.TestCase):
    @unittest.skipUnless(powershell_path(),'PowerShell is not installed')
    def test_native_guard_executes_height_reuse_and_rejection_paths(self):
        source=(BASE/'select_member.ps1').read_text(encoding='utf-8-sig')
        start=source.index("    $guardStage='layout'",source.index("if ($payload.mode -notin"))
        end=source.index('    if ($payload.target_member_only -ne $true)',start)
        block=source[start:end]
        base=premium.native_frame();capture=base['capture'];listing=base['regions']['list']
        name={'left':capture['left']+149/2,'top':capture['top']+340/2,'width':55/2,'height':30/2}
        cases=[]
        def case(label,*,change=None,target_only=True,row_error='',ok=False,stage=None,row_calls=0):
            payload={'target_member_only':target_only,'regions':copy.deepcopy(base['regions']),
                'x':math.floor(name['left']+name['width']/2),'y':math.floor(name['top']+name['height']/2),
                'name_bounds':name}
            fresh=copy.deepcopy(base)
            if change:change(payload,fresh)
            cases.append({'label':label,'payload':payload,'fresh':fresh,'row_error':row_error,
                'expected':{'ok':ok,'stage':stage,'row_calls':row_calls}})
        case('stable',ok=True,row_calls=1)
        grow=lambda p,f:f['regions']['list'].update(height=244)
        case('load_below_same_row',change=grow,ok=True,row_calls=1)
        case('shrink_same_row',change=lambda p,f:p['regions']['list'].update(height=244),ok=True,row_calls=1)
        case('other_scan_mode',change=grow,target_only=False,stage='layout')
        case('width_changed',change=lambda p,f:f['regions']['list'].update(width=listing['width']+1),stage='layout')
        case('top_changed',change=lambda p,f:f['regions']['list'].update(top=listing['top']+1),stage='layout')
        case('header_changed',change=lambda p,f:f['regions']['header'].update(height=75),stage='layout')
        case('query_region_changed',change=lambda p,f:f['regions']['search'].update(left=0),stage='layout')
        case('two_changes',change=lambda p,f:(grow(p,f),f['regions']['viewport'].update(top=0)),stage='layout')
        case('zero_height',change=lambda p,f:p['regions']['list'].update(height=0),stage='layout')
        case('clipped_target',change=lambda p,f:f['regions']['list'].update(height=30),stage='point')
        error='Contact row image changed. No click; inspect the current page.'
        case('changed_target_while_loading',change=grow,row_error=error,stage='layout',row_calls=1)
        case('changed_target_stable_layout',row_error=error,stage='row',row_calls=1)
        case('invalid_capture_during_loading',change=grow,row_error='Capture dimensions changed.',stage='row',row_calls=1)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'block.ps1').write_text(block,encoding='utf-8-sig')
            (path/'cases.json').write_text(json.dumps(cases),encoding='utf-8')
            harness=r'''param($Root,$Source)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors) > $null
if ($errors.Count) {throw ($errors | Out-String)}
Add-Type @'
public static class MemberInput {
    public static int Calls=0;
    public static string Error="";
    public static void SameRow(string before,string current,int ew,int eh,int x,int y,int w,int h) {
        Calls++;
        if (Error.Length>0) throw new System.Exception(Error);
    }
}
'@
$block=[scriptblock]::Create([IO.File]::ReadAllText((Join-Path $Root 'block.ps1')))
$cases=Get-Content -Raw (Join-Path $Root 'cases.json') | ConvertFrom-Json
$results=@()
foreach ($case in $cases) {
    $payload=$case.payload;$fresh=$case.fresh;$regionChanges=@();$guardStage='';$listHeightOnly=$false
    $beforePath='original.png';$guardPath='fresh.png'
    [MemberInput]::Calls=0;[MemberInput]::Error=$case.row_error
    $ok=$true;$errorText=''
    try {. $block} catch {$ok=$false;$errorText=$_.Exception.GetBaseException().Message}
    $results+=@{label=$case.label;ok=$ok;stage=$guardStage;row_calls=[MemberInput]::Calls;
        list_height_reused=$listHeightOnly;error=$errorText}
}
ConvertTo-Json -InputObject @($results) -Depth 12 -Compress
'''
            (path/'test.ps1').write_text(harness,encoding='utf-8-sig')
            env=dict(os.environ,XDG_CACHE_HOME=str(path/'cache'),XDG_CONFIG_HOME=str(path/'config'),XDG_DATA_HOME=str(path/'data'))
            run=subprocess.run([powershell_path(),'-NoLogo','-NoProfile','-File',str(path/'test.ps1'),
                str(path),str(BASE/'select_member.ps1')],capture_output=True,text=True,env=env,timeout=45)
            self.assertEqual(run.returncode,0,run.stderr+run.stdout)
            results=json.loads(run.stdout);self.assertEqual(len(results),len(cases))
            for case,result in zip(cases,results):
                with self.subTest(case=case['label']):
                    expected=case['expected'];self.assertEqual(result['ok'],expected['ok'],result)
                    self.assertEqual(result['row_calls'],expected['row_calls'],result)
                    if expected['stage']:self.assertEqual(result['stage'],expected['stage'],result)
                    if case['label']=='changed_target_while_loading':self.assertEqual(result['error'],'List layout changed. No click.')
                    if case['label'] in ('load_below_same_row','shrink_same_row'):self.assertTrue(result['list_height_reused'])


if __name__=='__main__':unittest.main()
