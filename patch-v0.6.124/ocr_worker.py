"""Offline secondary OCR diagnostic. No Windows input, selection or database code."""
import hashlib
import copy
import json
import math
from pathlib import Path
import sys

from visual_members import rect, contains, numeric_word

BASE=Path(__file__).resolve().parent


def header_words(report, image, detections):
    capture=rect(report['capture']);header=rect(report['regions'].get('header_ocr',report['regions']['header']))
    search=rect(report['regions']['search']);scale=float(report['scale'])
    if scale!=2 or image.size!=(int(capture[2]*scale),int(capture[3]*scale)):
        raise ValueError('截图尺寸与当前区域报告不一致')
    accepted=[];rejected=[]
    for text,score,box in detections:
        points=[(float(p[0]),float(p[1])) for p in box]
        if len(points)!=4 or not all(math.isfinite(v) for p in points for v in p):
            raise ValueError('备用识别返回无效边界')
        left=min(p[0] for p in points);top=min(p[1] for p in points)
        right=max(p[0] for p in points);bottom=max(p[1] for p in points)
        x=capture[0]+(left+right)/(2*scale);y=capture[1]+(top+bottom)/(2*scale)
        if not contains(header,x,y) or contains(search,x,y):continue
        record={'text':str(text).strip(),'score':float(score),'box':box}
        if not math.isfinite(float(score)) or not 0<=float(score)<=1:raise ValueError('备用识别返回无效置信度')
        area=(capture[0]+left/scale,capture[1]+top/scale,(right-left)/scale,(bottom-top)/scale)
        if (area[0]<header[0] or area[1]<header[1] or area[0]+area[2]>header[0]+header[2]
                or area[1]+area[3]>header[1]+header[3] or left<0 or top<0
                or right>image.width or bottom>image.height):
            if report.get('header_page') is True:continue  # clipped edge is read on an overlapping page
            record['reason']='识别框不完整位于顶部';rejected.append(record);continue
        crop=image.crop((math.floor(left),math.floor(top),math.ceil(right),math.ceil(bottom))).convert('RGB')
        if crop.width<3 or crop.height<3:
            record['reason']='识别框过小';rejected.append(record);continue
        pixels=list(crop.getdata());border=[]
        for row in range(crop.height):
            for col in range(crop.width):
                if row<2 or row>=crop.height-2 or col<2 or col>=crop.width-2:
                    border.append(pixels[row*crop.width+col])
        pale=lambda p:max(p)-min(p)<=10 and min(p)>=220 and max(p)<=253
        gray=sum(pale(p) for p in border)/len(border)
        dark=sum(max(p)<100 for p in pixels)/len(pixels)
        record.update(pale_gray_border=gray,dark_ink_fraction=dark)
        if gray<0.80 or dark<0.008:
            record['reason']='未确认浅灰标签背景和深色文字，可能为头像';rejected.append(record);continue
        if float(score)<0.80:
            record['reason']='置信度不足';rejected.append(record);continue
        accepted.append(record)
    return {'selected_numbers':[r['text'] for r in accepted if numeric_word(r['text'])],
        'other_header_words':[r['text'] for r in accepted if not numeric_word(r['text'])],
        'accepted':accepted,'rejected':rejected,'used_for_selection':False,
        'selection_verified':False,'final_invite_clicked':False,
        'reason':'只读备用图像证据；顶部核对由调用方绑定截图后判定，不代表身份核验或邀请成功'}


_INK_CACHE=[]


def recognize_header_ink(report,image,engine):
    """Reuse only exactly identical header pixels and geometry, never list OCR."""
    header_words(report,image,[])  # Always validate this capture first.
    capture=rect(report['capture']);header=rect(report['regions'].get('header_ocr',report['regions']['header']))
    search=rect(report['regions']['search']);scale=float(report['scale'])
    # Mask only the live search field; its changing query/caret is not a chip.
    from PIL import ImageDraw
    frame=image.convert('RGB').copy()
    drawing=ImageDraw.Draw(frame)
    sl=math.floor((search[0]-capture[0])*scale);st=math.floor((search[1]-capture[1])*scale)
    sr=math.ceil((search[0]+search[2]-capture[0])*scale);sb=math.ceil((search[1]+search[3]-capture[1])*scale)
    drawing.rectangle((sl,st,sr-1,sb-1),fill=(0,0,0))
    bounds=(math.ceil((header[0]-capture[0])*scale),math.ceil((header[1]-capture[1])*scale),
        math.floor((header[0]+header[2]-capture[0])*scale),math.floor((header[1]+header[3]-capture[1])*scale))
    key=(capture,header,search,scale,report.get('header_page'),report.get('window_handle'),
        report.get('process_id'),report.get('dialog_runtime_id'),hashlib.sha256(frame.crop(bounds).tobytes()).hexdigest())
    for cached_engine,cached_key,result in _INK_CACHE:
        if cached_engine is engine and cached_key==key:return copy.deepcopy(result)
    result=recognize_header_ink_uncached(report,image,engine)
    _INK_CACHE.append((engine,key,copy.deepcopy(result)))
    del _INK_CACHE[:-2]
    return result


def recognize_header_ink_uncached(report, image, engine):
    """Read small label text that the full-frame detector can miss.

    Candidate bounds come only from this capture's ink outside the live search
    field. The existing pale-gray background and dark-ink checks still apply;
    neither requested numbers nor avatar text supplies a recognition result.
    """
    import cv2
    import numpy as np
    capture=rect(report['capture']);header=rect(report['regions'].get('header_ocr',report['regions']['header']))
    search=rect(report['regions']['search']);scale=float(report['scale'])
    # Reuse dimension validation before any image processing.
    header_words(report,image,[])
    pixels=np.asarray(image.convert('RGB'))
    mask=np.zeros(pixels.shape[:2],dtype=np.uint8)
    left=max(0,math.ceil((header[0]-capture[0])*scale))
    top=max(0,math.ceil((header[1]-capture[1])*scale))
    right=min(image.width,math.floor((header[0]+header[2]-capture[0])*scale))
    bottom=min(image.height,math.floor((header[1]+header[3]-capture[1])*scale))
    mask[top:bottom,left:right]=(pixels[top:bottom,left:right].max(axis=2)<190)
    sl=max(0,math.floor((search[0]-capture[0])*scale))
    st=max(0,math.floor((search[1]-capture[1])*scale))
    sr=min(image.width,math.ceil((search[0]+search[2]-capture[0])*scale))
    sb=min(image.height,math.ceil((search[1]+search[3]-capture[1])*scale))
    mask[st:sb,sl:sr]=0
    _,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
    pieces=[tuple(map(int,row[:4])) for row in stats[1:] if row[4]>=3]
    if len(pieces)>240:raise ValueError('顶部文字区域过于复杂，停止核验')
    groups=[]
    for x,y,w,h in sorted(pieces,key=lambda p:p[0]):
        matches=[]
        for i,(gx,gy,gw,gh) in enumerate(groups):
            overlap=min(y+h,gy+gh)-max(y,gy)
            gap=max(x-(gx+gw),gx-(x+w),0)
            if overlap>=.5*min(h,gh) and gap<=.8*max(h,gh):matches.append(i)
        for i in reversed(matches):
            gx,gy,gw,gh=groups.pop(i)
            nx=min(x,gx);ny=min(y,gy)
            w=max(x+w,gx+gw)-nx;h=max(y+h,gy+gh)-ny;x=nx;y=ny
        groups.append((x,y,w,h))
    detections=[];diagnostics=[]
    for x,y,w,h in groups:
        if h<6:continue
        if report.get('header_page') is True and (y<=top or y+h>=bottom):continue
        # Anti-aliased glyph edges can extend beyond the dark-ink component.
        # Find a small surrounding border that meets the original background
        # check; do not lower its threshold or grow outside the live frame.
        candidate=None
        for padding in range(3,7):
            bounds=(x-padding,y-padding,x+w+padding,y+h+padding)
            box=[[bounds[0],bounds[1]],[bounds[2],bounds[1]],
                 [bounds[2],bounds[3]],[bounds[0],bounds[3]]]
            evidence=header_words(report,image,[('ink candidate',1.0,box)])
            if evidence['accepted']:
                candidate=(bounds,box,padding);break
        if candidate is None:continue
        bounds,box,padding=candidate
        crop=np.asarray(image.crop(bounds).convert('RGB'))[:,:,::-1].copy()
        result=engine(crop,use_det=False,use_cls=False,use_rec=True)
        texts=[] if result.txts is None else list(result.txts)
        scores=[] if result.scores is None else list(result.scores)
        if len(texts)!=1 or len(scores)!=1 or not str(texts[0]).strip():
            raise ValueError('顶部灰色标签存在未识别文字，停止核验')
        text=str(texts[0]).strip();score=float(scores[0])
        detections.append((text,score,box))
        diagnostics.append({'text':text,'score':score,'box':box,
            'padding':padding,'source':'gray_label_ink_crop'})
    return detections,diagnostics


def review_list_names(report,image,engine,detections,digest):
    """Independently reread low-score numeric text; never supply a target hint."""
    import numpy as np
    if not any(numeric_word(str(text).strip()) and score<.80 for text,score,box in detections):return []
    capture=rect(report['capture']);scale=float(report['scale'])
    areas=[rect(report['regions'][key]) for key in ('list','viewport')]
    left=max(0,max(math.ceil((a[0]-capture[0])*scale) for a in areas))
    top=max(0,max(math.ceil((a[1]-capture[1])*scale) for a in areas))
    right=min(image.width,min(math.floor((a[0]+a[2]-capture[0])*scale) for a in areas))
    bottom=min(image.height,min(math.floor((a[1]+a[3]-capture[1])*scale) for a in areas))
    reviews=[]
    for index,(text,score,box) in enumerate(detections):
        if not numeric_word(str(text).strip()) or score>=.80:continue
        xs=[float(p[0]) for p in box];ys=[float(p[1]) for p in box]
        x0=math.floor(min(xs));y0=math.floor(min(ys));x1=math.ceil(max(xs));y1=math.ceil(max(ys))
        if not (left<=x0<x1<=right and top<=y0<y1<=bottom):continue
        if len(reviews)>=8:raise ValueError('列表需要局部复核的数字过多，停止')
        reads=[]
        for padding in (4,8):
            bounds=(max(left,x0-padding),max(top,y0-padding),min(right,x1+padding),min(bottom,y1+padding))
            crop=image.crop(bounds).convert('RGB')
            result=engine(np.asarray(crop)[:,:,::-1].copy(),use_det=False,use_cls=False,use_rec=True)
            texts=[] if result.txts is None else list(result.txts)
            scores=[] if result.scores is None else list(result.scores)
            reads.append({'padding':padding,'crop_bounds':list(bounds),
                'text':str(texts[0]).strip() if len(texts)==1 else '',
                'score':float(scores[0]) if len(scores)==1 else 0})
        reviews.append({'source':'bound_list_name_crop','detection_index':index,'box':box,
            'image_sha256':digest,'reads':reads})
    return reviews


def merge_header_evidence(primary, supplemental):
    """Deduplicate the same label's overlapping bounds; contradictions stop."""
    accepted=list(primary['accepted']);rejected=list(primary['rejected'])
    def overlaps(a,b):
        ax=[p[0] for p in a['box']];ay=[p[1] for p in a['box']]
        bx=[p[0] for p in b['box']];by=[p[1] for p in b['box']]
        return min(max(ax),max(bx))>max(min(ax),min(bx)) and min(max(ay),max(by))>max(min(ay),min(by))
    for word in supplemental['accepted']:
        matches=[old for old in accepted if overlaps(old,word)]
        if matches:
            if len(matches)!=1 or matches[0]['text']!=word['text']:
                raise ValueError('顶部标签整图与局部识别不一致，停止核验')
        else:accepted.append(word)
    rejected.extend(supplemental['rejected'])
    accepted.sort(key=lambda r:(min(p[1] for p in r['box']),min(p[0] for p in r['box'])))
    result=dict(primary)
    result.update(accepted=accepted,rejected=rejected,
        selected_numbers=[r['text'] for r in accepted if numeric_word(r['text'])],
        other_header_words=[r['text'] for r in accepted if not numeric_word(r['text'])])
    return result


def bound_visible_header(report, summary, digest):
    """Check this frame's OCR with the same guard used by the caller."""
    from visual_members import selection_header_words
    current=copy.deepcopy(report)
    current['backup_ocr']=dict(summary,ok=True,image_sha256=digest)
    current['selection_header_ocr']={'ok':True,'used_for_selection':True,'image_sha256':digest,
        'frame':{key:copy.deepcopy(current[key]) for key in
            ('window_handle','process_id','dialog_runtime_id','capture','regions','scale')}}
    return selection_header_words(current,rect(current['capture']),
        {key:rect(current['regions'][key]) for key in ('header','search')},float(current['scale']))


def retry_new_member_crop(report, image, engine, summary, digest):
    """Only a missed new chip gets one recognition of its native OCR bounds.

    Native text locates pixels, but never supplies accepted label evidence.
    The independent recognizer receives only the crop, without a target hint.
    A successful first read returns before any additional recognition.
    """
    checkpoint=report.get('visible_header_checkpoint') or {}
    number=checkpoint.get('number')
    if (report.get('header_scan_only') is not True or checkpoint.get('stage')!='after'
            or number in summary['selected_numbers']):return summary
    try:
        bound_visible_header(report,summary,digest)
    except (KeyError,TypeError,ValueError) as error:
        if str(error)!='本次新增编号未完整可见，停止，不重复点击':return summary
        first_error=str(error)
    else:return summary
    retry={'scope':'new_member_native_crop_retry','number':number,'max_retries':1,
        'image_sha256':digest,'first':dict(copy.deepcopy(summary),ok=False,error=first_error)}
    original=summary
    try:
        native=report.get('ocr') or {}
        if native.get('available') is not True or not isinstance(native.get('words'),list):
            raise ValueError('新增备注没有可用的Windows数字位置，不推测标签')
        capture=rect(report['capture']);scale=float(report['scale'])
        header=rect(report['regions'].get('header_ocr',report['regions']['header']))
        search=rect(report['regions']['search'])
        candidates=[]
        for word in native['words']:
            if str(word.get('text','')).strip()!=number:continue
            x,y,w,h=rect(word)
            left=capture[0]+x/scale;top=capture[1]+y/scale
            right=left+w/scale;bottom=top+h/scale
            if (left<header[0] or top<header[1] or right>header[0]+header[2]
                    or bottom>header[1]+header[3]):continue
            if (left<search[0]+search[2] and right>search[0]
                    and top<search[1]+search[3] and bottom>search[1]):continue
            candidates.append((word,(x,y,w,h)))
        if len(candidates)!=1:raise ValueError('新增备注的Windows顶部数字位置不唯一或不可见')
        word,(x,y,w,h)=candidates[0]
        bounds=(math.floor(x)-4,math.floor(y)-4,math.ceil(x+w)+4,math.ceil(y+h)+4)
        retry.update(native_word=copy.deepcopy(word),crop_bounds=list(bounds),padding=4)
        left=capture[0]+bounds[0]/scale;top=capture[1]+bounds[1]/scale
        right=capture[0]+bounds[2]/scale;bottom=capture[1]+bounds[3]/scale
        if (bounds[0]<0 or bounds[1]<0 or bounds[2]>image.width or bounds[3]>image.height
                or left<header[0] or top<header[1] or right>header[0]+header[2]
                or bottom>header[1]+header[3]
                or (left<search[0]+search[2] and right>search[0]
                    and top<search[1]+search[3] and bottom>search[1])):
            raise ValueError('新增备注局部范围越界或覆盖搜索框')
        box=[[bounds[0],bounds[1]],[bounds[2],bounds[1]],
            [bounds[2],bounds[3]],[bounds[0],bounds[3]]]
        # Test only background/ink geometry here; the placeholder is not proof.
        if not header_words(report,image,[('crop geometry',1.0,box)])['accepted']:
            raise ValueError('新增备注局部未确认灰色标签与深色文字')
        import numpy as np
        crop=np.asarray(image.crop(bounds).convert('RGB'))[:,:,::-1].copy()
        result=engine(crop,use_det=False,use_cls=False,use_rec=True)
        texts=[] if result.txts is None else [str(text).strip() for text in result.txts]
        scores=[] if result.scores is None else [float(score) for score in result.scores]
        retry['second']={'texts':texts,'scores':scores,'box':box,
            'source':'bound_native_new_member_crop'}
        if len(texts)!=1 or len(scores)!=1 or texts[0]!=number:
            raise ValueError('新增备注局部识别未得到唯一完整目标数字')
        evidence=header_words(report,image,[(texts[0],scores[0],box)])
        retry['second']['evidence']=evidence
        if len(evidence['accepted'])!=1:raise ValueError('新增备注局部置信度或标签证据不足')
        merged=merge_header_evidence(summary,evidence)
        bound_visible_header(report,merged,digest)
        summary=merged
        retry['verified']=True
    except Exception as error:
        summary=original
        retry.update(verified=False,error=str(error))
    return dict(summary,new_member_retry=retry)


_RUNTIME=None

def runtime():
    global _RUNTIME
    if _RUNTIME is not None:return _RUNTIME
    from PIL import Image
    from rapidocr import RapidOCR
    from importlib.metadata import version
    manifest=json.loads((BASE/'ocr_models'/'manifest.json').read_text())
    models={}
    for name,expected in manifest.items():
        path=BASE/'ocr_models'/name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('识别模型文件损坏：'+name)
        models[name]=str(path)
    engine=RapidOCR(params={'Global.model_root_dir':str(BASE/'ocr_models'),'Global.log_level':'error',
        'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':2,
        'Det.model_path':models['PP-OCRv6_det_small.onnx'],
        'Rec.model_path':models['PP-OCRv6_rec_small.onnx'],
        'Cls.model_path':models['ch_ppocr_mobile_v2.0_cls_mobile.onnx']})
    _RUNTIME=(engine,version,Image)
    return _RUNTIME


def infer(report):
    if report.get('ok') is not True or report.get('read_only') is not True or report.get('scope')!='member_visual':
        raise ValueError('备用识别仅接受只读弹窗截图报告')
    engine,version,Image=runtime()
    if isinstance(report.get('header_scan_pages'),list):
        pages=report['header_scan_pages']
        if not 2<=len(pages)<=51:raise ValueError('滚动截图数量无效')
        return {'ok':True,'pages':[infer_capture(page,engine,version,Image) for page in pages],
            'final_invite_clicked':False}
    return infer_capture(report,engine,version,Image)


def header_crop_bounds(report,width,height):
    capture=rect(report['capture']);header=rect(report['regions'].get('header_ocr',report['regions']['header']))
    scale=float(report['scale'])
    if not math.isfinite(scale) or scale<=0:raise ValueError('截图比例无效')
    bounds=(max(0,math.floor((header[0]-capture[0])*scale)),
            max(0,math.floor((header[1]-capture[1])*scale)),
            min(width,math.ceil((header[0]+header[2]-capture[0])*scale)),
            min(height,math.ceil((header[1]+header[3]-capture[1])*scale)))
    if bounds[2]<=bounds[0] or bounds[3]<=bounds[1]:raise ValueError('顶部裁剪区域无效')
    return bounds


def infer_capture(report,engine,version,Image):
    if report.get('ok') is not True or report.get('read_only') is not True or report.get('scope')!='member_visual':
        raise ValueError('滚动页不是有效的只读截图')
    path=Path(report['image_path'])
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if report.get('progressive_member_ocr') is True and report.get('header_scan_only') is not True:
        return infer_member_rows(report,engine,version,Image,digest)
    # RapidOCR retains per-call flags. A previous ink crop disables detection;
    # every full frame must explicitly restore it when sharing one engine.
    import time
    started=time.perf_counter()
    offset=(0,0)
    if report.get('header_scan_only') is True:
        import numpy as np
        with Image.open(path) as image:
            bounds=header_crop_bounds(report,image.width,image.height)
            cropped=image.crop(bounds).convert('RGB')
            if report.get('header_retry_mask_search') is True:
                # Only a previous-chip retry masks the bound live query. No
                # selected number or expected glyph is supplied to the engine.
                from PIL import ImageDraw
                capture=rect(report['capture']);search=rect(report['regions']['search'])
                scale=float(report['scale'])
                mask=(max(0,math.floor((search[0]-capture[0])*scale)-bounds[0]),
                    max(0,math.floor((search[1]-capture[1])*scale)-bounds[1]),
                    min(cropped.width,math.ceil((search[0]+search[2]-capture[0])*scale)-bounds[0]),
                    min(cropped.height,math.ceil((search[1]+search[3]-capture[1])*scale)-bounds[1]))
                if mask[2]>mask[0] and mask[3]>mask[1]:
                    ImageDraw.Draw(cropped).rectangle((mask[0],mask[1],mask[2]-1,mask[3]-1),fill='white')
            cropped=np.asarray(cropped)
        offset=bounds[:2]
        result=engine(cropped,use_det=True,use_cls=False,use_rec=True)
    else:
        result=engine(str(path),use_det=True,use_cls=False,use_rec=True)
    detections=[]
    if result.txts is not None:
        detections=[(text,float(score),[[float(x)+offset[0],float(y)+offset[1]] for x,y in box.tolist()]) for box,text,score in zip(result.boxes,result.txts,result.scores)]
    with Image.open(path) as image:
        primary=header_words(report,image,detections)
        extra,diagnostics=recognize_header_ink(report,image,engine)
        summary=merge_header_evidence(primary,header_words(report,image,extra))
        if report.get('header_scan_only') is True:
            summary.update(detections=[{'text':text,'score':score,'box':box} for text,score,box in detections],
                header_crop_detections=diagnostics)
            summary=retry_new_member_crop(report,image,engine,summary,digest)
        list_reviews=[] if report.get('header_scan_only') is True else review_list_names(report,image,engine,detections,digest)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('备用识别期间截图文件改变')
    summary.update(ok=True,engine='RapidOCR CPU',engine_version=version('rapidocr'),
        runtime_version=version('onnxruntime'),image_sha256=digest,
        detections=[{'text':text,'score':score,'box':box} for text,score,box in detections],
        header_crop_detections=diagnostics,list_name_reviews=list_reviews,
        inference_seconds=round(time.perf_counter()-started,3),header_scan_only=report.get('header_scan_only') is True)
    if report.get('header_retry_mask_search') is True:
        summary['header_search_masked']=True
    return summary


def infer_member_rows(report,engine,version,Image,digest):
    """OCR row one first; recognize another crop only after a failed match."""
    import time
    import numpy as np
    from member_row_scan import member_row_crops,first_member_strip
    from visual_members import numeric_label,analyze_member_visual,selection_header_words
    frame_keys=('window_handle','process_id','dialog_runtime_id','capture','regions','scale')
    started=time.perf_counter();path=Path(report['image_path'])
    number=numeric_label(report['member_row_target'])
    def recognize(image,bounds):
        offset=bounds[:2]
        result=engine(np.asarray(image.crop(bounds).convert('RGB')),use_det=True,use_cls=False,use_rec=True)
        if result.txts is None:return []
        return [(text,float(score),[[float(x)+offset[0],float(y)+offset[1]] for x,y in box.tolist()])
            for box,text,score in zip(result.boxes,result.txts,result.scores)]
    def records(detections):
        return [{'text':text,'score':score,'box':box} for text,score,box in detections]
    def probe(summary,index,bounds):
        current=copy.deepcopy(report)
        current['backup_ocr']=dict(summary,ok=True,image_sha256=digest,
            member_row_scan={'policy':'first_then_fallback','number':number,
                'matched_row':index,'crop_bounds':bounds})
        current['selection_header_ocr']={'ok':True,'used_for_selection':True,'image_sha256':digest,
            'frame':{key:copy.deepcopy(current[key]) for key in frame_keys}}
        return current
    with Image.open(path) as image:
        crops=member_row_crops(report,image.width,image.height)
        strip=first_member_strip(report,image.width,image.height)
        if report.get('first_row_scan') is not True or report.get('ocr_scan_bounds')!=strip:
            raise ValueError('Windows识别范围未确认限制在第一行，请关闭助手后完整覆盖补丁')
        if (report.get('ocr') or {}).get('available') is not True:
            raise ValueError('第一行Windows识别未就绪：'+str((report.get('ocr') or {}).get('error') or '没有识别结果'))
        first=recognize(image,strip)
        primary=header_words(report,image,first)
        extra,diagnostics=recognize_header_ink(report,image,engine)
        header=merge_header_evidence(primary,header_words(report,image,extra))
        capture=rect(report['capture']);regions={k:rect(report['regions'][k]) for k in ('list','viewport','header','search')}
        scale=float(report['scale'])
        # A missed previous chip gets one header-only recognition on these
        # exact pixels. Other failures still stop before any extra list OCR.
        header_probe=probe(dict(header,detections=[]),1,crops[0])
        header_retry=None
        try:
            selection_header_words(header_probe,capture,regions,scale)
        except ValueError as error:
            checkpoint=report.get('visible_header_checkpoint') or {}
            if (str(error)!='上一位成功编号未完整可见，停止，不滚动或推测'
                    or checkpoint.get('stage')!='before' or not checkpoint.get('ledger')):
                raise
            header_retry={'scope':'previous_member_visible_retry','number':checkpoint['ledger'][-1],
                'target':number,'max_retries':1,'image_sha256':digest,
                'crop_bounds':list(header_crop_bounds(report,image.width,image.height)),
                'first':dict(header,ok=False,error=str(error),detections=records(first),
                    header_crop_detections=diagnostics)}
            retry_report=copy.deepcopy(report)
            retry_report.update(header_scan_only=True,header_retry_mask_search=True)
            retry_report.pop('progressive_member_ocr',None)
            retry_report.pop('member_row_target',None)
            try:
                reread=infer_capture(retry_report,engine,version,Image)
                header_retry['second']=reread
                selection_header_words(probe(reread,1,crops[0]),capture,regions,scale)
            except Exception as retry_error:
                if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                    raise ValueError('顶部复核期间截图文件改变') from retry_error
                header_retry.update(verified=False,error=str(retry_error))
                if 'second' not in header_retry:
                    header_retry['second']={'ok':False,'error':str(retry_error)}
                return dict(header,ok=False,error='上一位已选备注'+str(checkpoint['ledger'][-1])+
                    '顶部复核仍未通过：'+str(retry_error),used_for_selection=False,
                    selection_verified=False,final_invite_clicked=False,image_sha256=digest,
                    previous_member_retry=header_retry)
            header_retry['verified']=True
            # The new header evidence replaces the missed read; original row
            # one is reused, without another search, capture or list OCR.
            header=reread
            diagnostics=reread['header_crop_detections']
        def in_list(detection):
            _,_,box=detection
            xs=[p[0] for p in box];ys=[p[1] for p in box]
            x=capture[0]+(min(xs)+max(xs))/(2*scale)
            y=capture[1]+(min(ys)+max(ys))/(2*scale)
            return contains(regions['list'],x,y) and contains(regions['viewport'],x,y)
        header_detections=([d for d in first if not in_list(d)] if header_retry is None else
            [(d['text'],d['score'],d['box']) for d in header['detections']])
        scans=[];chosen=None;selected_bounds=None;final_detections=header_detections;final_reviews=[]
        for index,bounds in enumerate(crops,1):
            row=[d for d in first if in_list(d)] if index==1 else recognize(image,bounds)
            detections=header_detections+row
            reviews=review_list_names(report,image,engine,detections,digest)
            summary=dict(header,detections=records(detections),list_name_reviews=reviews)
            analysis=analyze_member_visual(probe(summary,index,bounds),number)
            matched=analysis['usable'] and analysis['list_matches']==1
            scans.append({'row':index,'crop_bounds':bounds,'ocr_bounds':strip if index==1 else bounds,
                'detections':records(row),'analysis':analysis,'matched':bool(matched)})
            if matched:
                chosen=index;selected_bounds=bounds;final_detections=detections;final_reviews=reviews
                break
        summary=dict(header)
        summary.update(ok=True,engine='RapidOCR CPU',engine_version=version('rapidocr'),
            runtime_version=version('onnxruntime'),image_sha256=digest,
            detections=records(final_detections),header_crop_detections=diagnostics,
            list_name_reviews=final_reviews,inference_seconds=round(time.perf_counter()-started,3),
            header_scan_only=False,member_row_scan={'policy':'first_then_fallback','number':number,
                'matched_row':chosen,'crop_bounds':selected_bounds,'scans':scans})
        if header_retry is not None:summary['previous_member_retry']=header_retry
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('逐行识别期间截图文件改变')
    return summary


def serve(input_stream,output_stream):
    # One JSON request per line. Every response echoes an unpredictable request ID.
    for line in input_stream:
        request=None
        try:
            request=json.loads(line)
            result=infer(request['report'])
        except Exception as error:
            result={'ok':False,'error':str(error),'used_for_selection':False,
                'selection_verified':False,'final_invite_clicked':False}
        result['request_id']=request.get('request_id') if isinstance(locals().get('request'),dict) else None
        output_stream.write(json.dumps(result,ensure_ascii=False)+'\n');output_stream.flush()


if __name__=='__main__':
    if '--serve' in sys.argv:
        serve(sys.stdin,sys.stdout)
    else:
        try:
            print(json.dumps(infer(json.load(sys.stdin)),ensure_ascii=False))
        except Exception as error:
            print(json.dumps({'ok':False,'error':str(error),'used_for_selection':False,
                'selection_verified':False,'final_invite_clicked':False},ensure_ascii=False))
            sys.exit(1)
