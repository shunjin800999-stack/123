"""Classify OCR evidence and plan one bounded list click; no input or DB writes."""
import math
import re
from collections import Counter


def corroborated_header_duplicate(word, backup):
    """A strong bound crop may supersede the same low-confidence full-frame word."""
    if word.get('reason')!='置信度不足' or not numeric_word(str(word.get('text',''))):return False
    if not any(all(d.get(k)==word.get(k) for k in ('text','score','box')) for d in backup.get('detections',[])):return False
    try:
        def bounds(points):
            if len(points)!=4 or any(len(p)!=2 for p in points):raise ValueError('box')
            xs=[float(p[0]) for p in points];ys=[float(p[1]) for p in points]
            if not all(math.isfinite(v) for v in xs+ys):raise ValueError('box')
            return min(xs),min(ys),max(xs),max(ys)
        a=bounds(word['box'])
        for accepted in backup.get('accepted',[]):
            if accepted.get('text')!=word.get('text') or float(accepted.get('score',0))<.80:continue
            if not any(c.get('source')=='gray_label_ink_crop' and all(c.get(k)==accepted.get(k)
                       for k in ('text','score','box')) for c in backup.get('header_crop_detections',[])):continue
            b=bounds(accepted['box'])
            aa=(a[2]-a[0])*(a[3]-a[1]);bb=(b[2]-b[0])*(b[3]-b[1])
            overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
            if aa>0 and bb>0 and overlap/min(aa,bb)>=.9 and overlap/max(aa,bb)>=.6:return True
    except (KeyError,TypeError,ValueError):return False
    return False


def avatar_crop_fragment(word, backup):
    """Only reject a crop artifact in the avatar lane of a proven numeric chip."""
    if word.get('reason')!='置信度不足':return False
    crops=backup.get('header_crop_detections',[])
    if not any(c.get('source')=='gray_label_ink_crop' and
               all(c.get(k)==word.get(k) for k in ('text','score','box')) for c in crops):return False
    try:
        xs=[float(p[0]) for p in word['box']];ys=[float(p[1]) for p in word['box']]
        left,right,top,bottom=min(xs),max(xs),min(ys),max(ys)
        if not all(math.isfinite(x) for x in (left,right,top,bottom)) or right<=left or bottom<=top:return False
        for chip in backup.get('accepted',[]):
            if not numeric_word(str(chip.get('text',''))) or float(chip.get('score',0))<.80:continue
            ax=[float(p[0]) for p in chip['box']];ay=[float(p[1]) for p in chip['box']]
            height=max(ay)-min(ay)
            gap=min(ax)-right
            # The avatar is immediately left of the name, on the same row.
            if (height>0 and 0<gap<=height and right-left<=height and bottom-top<=height
                    and top<max(ay) and bottom>min(ay)
                    and abs((top+bottom-min(ay)-max(ay))/2)<=height):return True
    except (KeyError,TypeError,ValueError):return False
    return False


def rect(value):
    numbers=tuple(float(value[key]) for key in ('left','top','width','height'))
    if not all(math.isfinite(x) for x in numbers) or numbers[2]<=0 or numbers[3]<=0:
        raise ValueError('图像区域尺寸无效')
    return numbers


def contains(area, x, y):
    left,top,width,height=area
    return left<=x<left+width and top<=y<top+height


def numeric_label(number):
    """Preserve explicit legacy names; integers use the new unpadded format."""
    name=str(number).strip()
    if not re.fullmatch(r'[0-9]{1,6}',name) or not 1<=int(name)<=999999:
        raise ValueError('数字备注为 1–999999；请按联系人实际备注填写，例如 1 或 001')
    return name


def numeric_word(label):
    return bool(re.fullmatch(r'[0-9]{1,6}',label)) and int(label)>0


def member_labels(values, allow_empty=False):
    labels=[numeric_label(value) for value in values]
    if not labels and not allow_empty:raise ValueError('选人名单不能为空')
    if len({int(label) for label in labels})!=len(labels):raise ValueError('备注编号重复；1 和 001 也不能同时使用')
    return labels


def parse_member_labels(text):
    values=[]
    for token in re.split(r'[,，、;；\s]+',str(text).strip()):
        if not token:continue
        interval=re.fullmatch(r'([1-9][0-9]{0,5})-([1-9][0-9]{0,5})',token)
        if interval:
            start,end=map(int,interval.groups())
            if end<start or end-start>=40:raise ValueError('连续编号范围每次最多 40 位，例如 1-20')
            values.extend(str(value) for value in range(start,end+1))
        else:values.append(numeric_label(token))
    return member_labels(values)


def same_text_line(first, second):
    # Relative overlap handles font sizes and window scaling; no fixed screen positions.
    a=first['bounds'];b=second['bounds']
    overlap=min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1])
    return overlap>=0.5*min(a[3],b[3])


def list_text_lines(words):
    """Keep surrounding text so dates/status fragments cannot become contact names."""
    groups=[]
    for word in sorted(words,key=lambda word:word['bounds'][1]):
        matches=[group for group in groups if any(same_text_line(word,other) for other in group)]
        if not matches:
            groups.append([word]);continue
        merged=[word]
        for group in matches:
            merged.extend(group);groups.remove(group)
        groups.append(merged)
    return groups


def visible_selection_header_words(report,capture,regions,scale):
    """Validate OCR against its capture and live frame; never guess bad glyphs."""
    proof=report['selection_header_ocr'];backup=report.get('backup_ocr') or {}
    if proof.get('ok') is not True or proof.get('used_for_selection') is not True:
        raise ValueError('备用顶部核对失败：'+str(proof.get('error') or '证据不完整'))
    digest=proof.get('image_sha256','')
    if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest) or digest!=backup.get('image_sha256'):
        raise ValueError('备用顶部证据与截图校验不一致')
    frame=proof.get('frame') or {}
    for key in ('window_handle','process_id','dialog_runtime_id','capture','regions','scale'):
        if report.get(key) is None or frame.get(key)!=report[key]:raise ValueError('备用顶部证据与当前弹窗区域不一致')
    if not frame['dialog_runtime_id'] or backup.get('ok') is not True or backup.get('final_invite_clicked') is not False:
        raise ValueError('备用顶部证据状态无效')
    selected=[];other=[]
    header_area=rect(report['regions'].get('header_ocr',report['regions']['header']))
    if not isinstance(backup.get('accepted'),list) or not isinstance(backup.get('rejected'),list):
        raise ValueError('缺少备用识别原始候选证据')
    for word in backup['accepted']:
        score=float(word['score']);gray=float(word['pale_gray_border']);dark=float(word['dark_ink_fraction'])
        if not all(math.isfinite(v) and 0<=v<=1 for v in (score,gray,dark)) or score<.80 or gray<.8 or dark<.008:
            raise ValueError('备用顶部备注置信度或字形证据不足')
        points=word['box']
        if len(points)!=4 or any(len(p)!=2 for p in points):raise ValueError('备用顶部边界无效')
        xs=[float(p[0]) for p in points];ys=[float(p[1]) for p in points]
        if not all(math.isfinite(v) for v in xs+ys):raise ValueError('备用顶部边界无效')
        left=capture[0]+min(xs)/scale;top=capture[1]+min(ys)/scale
        right=capture[0]+max(xs)/scale;bottom=capture[1]+max(ys)/scale
        if right<=left or bottom<=top:raise ValueError('备用顶部边界无效')
        for area in (capture,header_area):
            if left<area[0] or top<area[1] or right>area[0]+area[2] or bottom>area[1]+area[3]:
                raise ValueError('备用备注未完整位于顶部')
        if contains(regions['search'],(left+right)/2,(top+bottom)/2):raise ValueError('备用备注位于搜索框')
        label=str(word['text']).strip()
        if not label:raise ValueError('备用顶部文字为空')
        (selected if numeric_word(label) else other).append(label)
    for word in backup['rejected']:
        if avatar_crop_fragment(word,backup) or corroborated_header_duplicate(word,backup):continue
        if word.get('reason') in ('识别框不完整位于顶部','识别框过小') or (
                float(word.get('pale_gray_border',0))>=.8 and float(word.get('dark_ink_fraction',0))>=.008):
            raise ValueError('备用顶部仍有无法完整核对的标签候选')
    if selected!=backup.get('selected_numbers') or other!=backup.get('other_header_words'):
        raise ValueError('备用顶部名单与原始候选证据不一致')
    return selected,other


def selection_header_words(report,capture,regions,scale):
    visible=visible_selection_header_words(report,capture,regions,scale)
    checkpoint=report.get('visible_header_checkpoint')
    if checkpoint is not None:
        ledger=member_labels(checkpoint['ledger'],allow_empty=True)
        name=numeric_label(checkpoint['number']);stage=checkpoint['stage']
        if checkpoint.get('scope')!='visible_pair' or stage not in ('before','after') or name in ledger:
            raise ValueError('快速核对阶段或本次编号无效')
        from selection_header_scan import geometry,page_area
        _,_,offset,maximum=geometry(report,minimum_height=24)
        state=report['header_scroll']
        if report['regions'].get('header_ocr')!=page_area(report,minimum_height=24):
            raise ValueError('快速核对可见范围未绑定当前窗口')
        if offset!=maximum:
            raise ValueError('快速核对需要当前底部可见区域，不自动滚动')
        selected,other=visible
        if other or len(selected)!=len(set(selected)) or not set(selected)<=set(ledger+([name] if stage=='after' else [])):
            raise ValueError('快速核对发现未知、重复或未确认的已选编号')
        if stage=='before' and ledger and selected.count(ledger[-1])!=1:
            raise ValueError('上一位成功编号未完整可见，停止，不滚动或推测')
        if stage=='before' and not ledger and (selected or state.get('max_offset',0)!=0):
            raise ValueError('快速选人必须从空白选择开始')
        if stage=='after' and selected.count(name)!=1:
            raise ValueError('本次新增编号未完整可见，停止，不重复点击')
        return visible
    if 'full_header_scan' not in report:
        state=report.get('header_scroll')
        if state and float(state['max_offset'])>0:
            raise ValueError('已选区域存在被遮住的标签，必须滚动完整读取')
        return visible
    from selection_header_scan import validate_full_header
    return validate_full_header(report)


def member_header_guard(report):
    """Allow the observed caret overhang only when no selected label is masked."""
    if 'selection_header_ocr' not in report:
        return {'search_caret_margin':0,'selected_name_bounds':[],'selected_label_count':0}
    capture=rect(report['capture']);scale=float(report['scale'])
    regions={key:rect(report['regions'][key]) for key in ('header','search')}
    selected,other=visible_selection_header_words(report,capture,regions,scale)
    if other:raise ValueError('顶部存在其他文字备注，不能扩大搜索光标排除范围')
    search=regions['search'];header=regions['header'];margin=2
    mask=(max(capture[0],header[0],search[0]-margin),max(capture[1],header[1],search[1]-margin),
        min(capture[0]+capture[2],header[0]+header[2],search[0]+search[2]+margin),
        min(capture[1]+capture[3],header[1]+header[3],search[1]+search[3]+margin))
    bounds=[]
    for word in report['backup_ocr']['accepted']:
        xs=[float(p[0]) for p in word['box']];ys=[float(p[1]) for p in word['box']]
        left=capture[0]+min(xs)/scale;top=capture[1]+min(ys)/scale
        right=capture[0]+max(xs)/scale;bottom=capture[1]+max(ys)/scale
        if left<mask[2] and right>mask[0] and top<mask[3] and bottom>mask[1]:
            raise ValueError('搜索光标排除范围接触已选备注，停止，不忽略标签文字')
        bounds.append({'left':left,'top':top,'width':right-left,'height':bottom-top})
    if len(bounds)!=len(selected):raise ValueError('已选备注保护范围不完整')
    return {'search_caret_margin':margin,'selected_name_bounds':bounds,'selected_label_count':len(selected)}


def rapid_member_candidates(report,name,capture,regions,scale):
    """Exact names in the bound capture, above an aligned status line.

    Status anchors are whole English last-seen/online lines or the Portuguese
    date, minutes/hours-ago, last-week and recently labels observed in native reports.
    Geometry is relative to text bounds; avatar content is never a name anchor.
    Only words wholly left of the aligned status text column are excluded from
    the name line; adjacent or overlapping text fragments still reject it.
    Caller must validate the capture binding with selection_header_words first.
    """
    detections=report['backup_ocr'].get('detections',[])
    if not isinstance(detections,list):raise ValueError('备用列表原始识别数据无效')
    words=[]
    for word in detections:
        points=word['box'];score=float(word['score'])
        if len(points)!=4 or any(len(p)!=2 for p in points):raise ValueError('备用列表边界无效')
        xs=[float(p[0]) for p in points];ys=[float(p[1]) for p in points]
        if not all(math.isfinite(v) for v in xs+ys+[score]) or not 0<=score<=1:
            raise ValueError('备用列表边界或置信度无效')
        left,top,right,bottom=min(xs),min(ys),max(xs),max(ys)
        if right<=left or bottom<=top:raise ValueError('备用列表边界无效')
        bounds=(capture[0]+left/scale,capture[1]+top/scale,(right-left)/scale,(bottom-top)/scale)
        if any(bounds[0]<a[0] or bounds[1]<a[1] or bounds[0]+bounds[2]>a[0]+a[2]
                or bounds[1]+bounds[3]>a[1]+a[3] for a in (capture,regions['list'],regions['viewport'])):
            continue
        words.append({'label':str(word['text']).strip(),'bounds':bounds,'score':score,'raw_box':word['box']})
    statuses=[w for w in words if w['score']>=.80 and re.fullmatch(
        r'(?:last\s+seen\s+\S[\s\S]*|online|visto\s+(?:'
        r'em\s+\d{4}\s*/\s*\d{1,2}\s*/\s*\d{1,2}|'
        r'há\s+\d+\s+(?:horas?|minutos?)|'
        r'(?:ontem|hoje)\s+às\s+(?:[01]?\d|2[0-3]):[0-5]\d|'
        r'na\s+última\s+semana|recentemente|agora\s+mesmo|há\s+muito\s+tempo))',
        w['label'],re.IGNORECASE)]
    candidates=[]
    for word in words:
        if word['label']!=name:continue
        left,top,width,height=word['bounds']
        anchors=[s for s in statuses if 0<=s['bounds'][1]-(top+height)<=height*1.5
            and abs(s['bounds'][0]-left)<=min(height,s['bounds'][3])*.5]
        if len(anchors)!=1:continue
        # The status supplies the live text-column boundary. An avatar's digit
        # can overlap the name vertically but sits entirely left of this column.
        # Retain all words touching the column, including split digits/punctuation.
        status=anchors[0]
        text_left=status['bounds'][0]-min(height,status['bounds'][3])*.5
        text_words=[w for w in words if w['bounds'][0]+w['bounds'][2]>=text_left]
        line=next(line for line in list_text_lines(text_words) if word in line)
        if len(line)!=1:continue
        if word['score']<.80:
            raw_index=next((i for i,d in enumerate(detections) if d['text'].strip()==word['label'] and
                d['box']==word['raw_box']),None)
            reviews=[r for r in report['backup_ocr'].get('list_name_reviews',[])
                if r.get('detection_index')==raw_index]
            if len(reviews)!=1:raise ValueError('备用列表存在低置信度的同备注候选，停止，不猜文字')
            review=reviews[0];reads=review.get('reads',[])
            if (review.get('source')!='bound_list_name_crop' or review.get('box')!=word['raw_box']
                    or review.get('image_sha256')!=report['backup_ocr'].get('image_sha256')
                    or len(reads)!=2 or [r.get('padding') for r in reads]!=[4,8]):
                raise ValueError('局部列表数字复核绑定不完整')
            # Recompute each crop from the ORIGINAL raw box and current list.
            xs=[p[0] for p in word['raw_box']];ys=[p[1] for p in word['raw_box']]
            cl=max(0,max(math.ceil((a[0]-capture[0])*scale) for a in (regions['list'],regions['viewport'])))
            ct=max(0,max(math.ceil((a[1]-capture[1])*scale) for a in (regions['list'],regions['viewport'])))
            cr=min(capture[2]*scale,min(math.floor((a[0]+a[2]-capture[0])*scale) for a in (regions['list'],regions['viewport'])))
            cb=min(capture[3]*scale,min(math.floor((a[1]+a[3]-capture[1])*scale) for a in (regions['list'],regions['viewport'])))
            for read in reads:
                pad=read['padding'];expected=[max(cl,math.floor(min(xs))-pad),max(ct,math.floor(min(ys))-pad),
                    min(cr,math.ceil(max(xs))+pad),min(cb,math.ceil(max(ys))+pad)]
                confidence=float(read.get('score',0))
                if (read.get('crop_bounds')!=expected or read.get('text')!=name
                        or not math.isfinite(confidence) or not .80<=confidence<=1):
                    raise ValueError('局部列表数字未两次高置信度一致，停止，不点击')
        candidates.append(dict(zip(('left','top','width','height'),word['bounds'])))
    return candidates


def same_name_bounds(a,b):
    ax,ay,aw,ah=rect(a);bx,by,bw,bh=rect(b)
    return (min(ax+aw,bx+bw)>max(ax,bx) and min(ay+ah,by+bh)>max(ay,by)
        and abs(ay+ah/2-by-bh/2)<=max(ah,bh)*.5)


def analyze_member_visual(report, number):
    name=numeric_label(number)
    result={'number':name,'usable':False,'list_matches':0,'chip_matches':0,
            'selected_numbers':[],'ignored_list_fragments':0,
            'list_candidates':[],'other_header_words':[],
            'list_match_policy':'standalone_numeric_line','reason':''}
    if report.get('ok') is not True or report.get('read_only') is not True or report.get('scope')!='member_visual':
        result['reason']='不是有效的只读图像报告';return result
    ocr=report.get('ocr') or {}
    if ocr.get('available') is not True:
        result['reason']='文字识别不可用：'+str(ocr.get('error') or '未返回识别结果');return result
    try:
        capture=rect(report['capture'])
        scale=float(report['scale'])
        if not math.isfinite(scale) or scale<=0:raise ValueError('图像比例无效')
        regions={key:rect(report['regions'][key]) for key in ('list','viewport','header','search')}
        text=re.sub(r'\s+',' ',str(ocr.get('text') or '')).casefold()
        # Windows OCR can split the title's Add into Ad + d. Accept only this
        # observed spacing difference; numeric contact labels stay exact.
        if (not re.search(r'\b(?:add|ad\s+d)\s+members\b',text)
                and not re.search(r'\badicionar\s+(?:membros|participantes)\b',text)
                and '添加成员' not in text):
            result['reason']='截图中未识别到 Add Members 标题；可能为空白或截图失败';return result
        list_words=[]
        for word in ocr.get('words',[]):
            label=str(word.get('text') or '').strip()
            if not label:continue
            left,top,width,height=rect(word)
            x=capture[0]+(left+width/2)/scale
            y=capture[1]+(top+height/2)/scale
            if not contains(capture,x,y):continue
            if contains(regions['header'],x,y) and not contains(regions['search'],x,y):
                # Exact numeric labels only. Do not reinterpret O/0, I/1 or partial words.
                if not numeric_word(label):
                    result['other_header_words'].append(label);continue
                result['selected_numbers'].append(label)
                result['chip_matches']+=int(label==name)
            elif contains(regions['list'],x,y) and contains(regions['viewport'],x,y):
                list_words.append({'label':label,'bounds':(left,top,width,height)})
        for line in list_text_lines(list_words):
            # Require the entire line to be one complete numeric word. Date digits,
            # fragments in last-seen text and split names are not usable candidates.
            if len(line)==1 and numeric_word(line[0]['label']):
                result['list_matches']+=int(line[0]['label']==name)
                if line[0]['label']==name:
                    left,top,width,height=line[0]['bounds']
                    result['list_candidates'].append({'left':capture[0]+left/scale,
                        'top':capture[1]+top/scale,'width':width/scale,'height':height/scale})
            else:
                result['ignored_list_fragments']+=sum(word['label']==name for word in line)
        if 'selection_header_ocr' in report:
            selected,other=selection_header_words(report,capture,regions,scale)
            result.update(selected_numbers=selected,other_header_words=other,
                chip_matches=selected.count(name),header_source='RapidOCR bound capture')
            rapid=rapid_member_candidates(report,name,capture,regions,scale)
            result['rapid_list_candidates']=rapid
            if rapid:
                # Union, never replace: a second match missed by one OCR still
                # makes the list ambiguous. Overlapping observations count once.
                for candidate in rapid:
                    overlapping=[b for b in result['list_candidates'] if same_name_bounds(b,candidate)]
                    if len(overlapping)>1:raise ValueError('两个列表识别来源无法明确对应联系人行')
                    if not overlapping:result['list_candidates'].append(candidate)
                result['list_matches']=len(result['list_candidates'])
                result['list_source']='Windows OCR + bound RapidOCR status-aligned names'
        result['usable']=True
        result['reason']='仅为文字识别诊断，不代表身份核验或群邀请成功'
    except (KeyError,TypeError,ValueError,OverflowError) as error:
        result['usable']=False;result['list_matches']=0;result['chip_matches']=0;result['selected_numbers']=[];result['ignored_list_fragments']=0
        result['list_candidates']=[];result['other_header_words']=[]
        result['reason']='图像报告格式无效：'+str(error)
    return result


def selected_row_supported(analysis):
    # Some Desktop builds clear the query and keep selected rows in the list.
    # A retained row needs the independently bound, background-checked header
    # label; legacy Windows-only header OCR keeps its original stricter rule.
    return (analysis['list_matches']==0 or (analysis['list_matches']==1
        and analysis['chip_matches']==1 and analysis.get('header_source')=='RapidOCR bound capture'))


def plan_member_selection(report, number, expected_selected=()):
    """Fresh evidence only; no toggling an already selected member or ambiguous names."""
    analysis=analyze_member_visual(report,number)
    name=analysis['number']
    expected=member_labels(expected_selected,allow_empty=True)
    if name in expected:raise ValueError('目标已经在本次已核对名单中，不应再次点击')
    if not analysis['usable']:raise ValueError(analysis['reason'])
    if analysis['other_header_words']:raise ValueError('顶部存在其他文字备注，停止核查')
    selected=analysis['selected_numbers']
    if Counter(selected)==Counter(expected+[name]) and selected_row_supported(analysis):
        return {'action':'already_selected','number':name,'analysis':analysis}
    if Counter(selected)!=Counter(expected):raise ValueError('已选备注与刚才核对的名单不一致，停止，不取消任何人')
    if analysis['list_matches']!=1 or len(analysis['list_candidates'])!=1:
        raise ValueError('列表必须只有一位完整匹配的数字备注；未找到或重名时不点击')
    bounds=analysis['list_candidates'][0]
    left,top,width,height=rect(bounds)
    x=math.floor(left+width/2);y=math.floor(top+height/2)
    for key in ('list','viewport'):
        region=rect(report['regions'][key])
        if not contains(region,x,y) or left<region[0] or top<region[1] or left+width>region[0]+region[2] or top+height>region[1]+region[3]:
            raise ValueError('备注未完整位于可见联系人列表中，不点击')
    if not contains(rect(report['capture']),x,y):raise ValueError('点击位置超出弹窗')
    for key in ('header','search'):
        if contains(rect(report['regions'][key]),x,y):raise ValueError('点击位置属于搜索框或顶部区域')
    # A click requires metadata from the new live capture, never an imported report.
    if not isinstance(report.get('dialog_runtime_id'),str) or not report['dialog_runtime_id']:
        raise ValueError('缺少实时弹窗身份；请用新版重新运行单人选择测试')
    for key in ('add','cancel'):
        if contains(rect(report['button_regions'][key]),x,y):raise ValueError('点击位置属于最终按钮')
    return {'action':'click_once','number':name,'x':x,'y':y,'name_bounds':bounds,'analysis':analysis}


def verify_member_selection(before, after, number, expected_selected=()):
    """A selected chip must be new in the same dialog/window; invitation is unproven."""
    name=numeric_label(number)
    expected=member_labels(expected_selected,allow_empty=True)
    a=analyze_member_visual(before,name);b=analyze_member_visual(after,name)
    identity=all(before.get(key) is not None and before.get(key)==after.get(key)
                 for key in ('window_handle','process_id','dialog_runtime_id','capture'))
    confirmed=(identity and name not in expected and a['usable'] and Counter(a['selected_numbers'])==Counter(expected) and not a['other_header_words']
        and a['list_matches']==1 and b['usable'] and Counter(b['selected_numbers'])==Counter(expected+[name])
        and not b['other_header_words'] and selected_row_supported(b)
        and before.get('final_invite_clicked') is False and after.get('final_invite_clicked') is False)
    return {'selection_verified':bool(confirmed),'analysis':b,'final_invite_clicked':False,
        'reason':'已核对顶部出现该数字备注，停在 Add 前' if confirmed else
        '选中结果尚未核对通过；停止，不自动再次点击，请查看 Telegram 和截图'}


def verify_visible_member_selection(before,after,number,expected_selected):
    name=numeric_label(number);ledger=member_labels(expected_selected,allow_empty=True)
    a=analyze_member_visual(before,name);b=analyze_member_visual(after,name)
    identity=all(before.get(k) is not None and before.get(k)==after.get(k)
                 for k in ('window_handle','process_id','dialog_runtime_id','capture'))
    checkpoints=all(r.get('visible_header_checkpoint')=={'scope':'visible_pair','ledger':ledger,
        'number':name,'stage':stage} for r,stage in ((before,'before'),(after,'after')))
    confirmed=(identity and checkpoints and a['usable'] and b['usable'] and a['list_matches']==1
        and name not in a['selected_numbers'] and b['selected_numbers'].count(name)==1
        and selected_row_supported(b) and before.get('final_invite_clicked') is False and after.get('final_invite_clicked') is False)
    return {'selection_verified':bool(confirmed),'analysis':b,'final_invite_clicked':False,
        'reason':'点击前上一位、点击后新增编号核对通过；完整名单留到结束核对' if confirmed else '可见编号核对失败，停止，不滚动或重复点击'}
