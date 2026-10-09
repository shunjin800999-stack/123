"""Read every current selected chip across a bounded native scroll viewport.

No expected names are supplied to OCR. Overlap is deduplicated by position,
not by label. One complete live pass binds all visible pages before selection continues.
"""
import copy
import hashlib
import math
from pathlib import Path

from visual_members import rect, visible_selection_header_words

MARGIN=10
MAX_LABEL_HEIGHT=24
BIND_KEYS=('window_handle','process_id','dialog_runtime_id','capture','scale')
CONTROL_KEYS=('inner_runtime_id','viewport_runtime_id','scroll_runtime_id','search_runtime_id','search_value')


def geometry(report,*,minimum_height=64):
    state=report['header_scroll']
    inner=rect(state['inner']);view=rect(state['viewport'])
    offset=float(state['offset']);maximum=float(state['max_offset'])
    if not all(math.isfinite(v) for v in (offset,maximum)) or not 0<=offset<=maximum<=1000:
        raise ValueError('已选区域滚动范围无效')
    if (view[3]<minimum_height or inner[0]!=view[0] or inner[2]!=view[2] or inner[1]+offset!=view[1]
            or inner[3]-view[3]!=maximum or rect(report['regions']['header'])!=view):
        raise ValueError('已选区域内容与可见范围不一致')
    for key in CONTROL_KEYS:
        if not isinstance(state[key],str) or (key!='search_value' and not state[key]):
            raise ValueError('缺少已选区域控件绑定')
    return inner,view,offset,maximum


def page_area(report,*,minimum_height=64):
    _,view,offset,maximum=geometry(report,minimum_height=minimum_height)
    top=MARGIN if offset>0 else 0
    bottom=MARGIN if offset<maximum else 0
    return {'left':view[0],'top':view[1]+top,'width':view[2],'height':view[3]-top-bottom}


def bind_page(report,backup,digest):
    from backup_ocr import FRAME_KEYS
    page=copy.deepcopy(report)
    page['header_page']=True
    page['regions']['header_ocr']=page_area(page)
    page['backup_ocr']=copy.deepcopy(backup)
    page['selection_header_ocr']={'ok':True,'used_for_selection':True,'image_sha256':digest,
        'frame':{key:copy.deepcopy(page[key]) for key in FRAME_KEYS}}
    return page


def same_place(a,b):
    return (abs(a[0]+a[2]/2-b[0]-b[2]/2)<=4 and abs(a[1]+a[3]/2-b[1]-b[3]/2)<=4
        and abs(a[2]-b[2])<=8 and abs(a[3]-b[3])<=8)


def observations(page):
    capture=rect(page['capture']);scale=float(page['scale'])
    regions={k:rect(page['regions'][k]) for k in ('header','search')}
    visible_selection_header_words(page,capture,regions,scale)
    inner,_,_,_=geometry(page)
    rows=[]
    for word in page['backup_ocr']['accepted']:
        xs=[float(p[0]) for p in word['box']];ys=[float(p[1]) for p in word['box']]
        box=(capture[0]+min(xs)/scale-inner[0],capture[1]+min(ys)/scale-inner[1],
            (max(xs)-min(xs))/scale,(max(ys)-min(ys))/scale)
        if box[3]>MAX_LABEL_HEIGHT:raise ValueError('标签高度超过重叠核对范围')
        rows.append((word['text'].strip(),box))
    return rows


def validate_pass(pages,reference):
    if not isinstance(pages,list) or not 2<=len(pages)<=25:raise ValueError('滚动页证据数量无效')
    ri,rv,_,maximum=geometry(reference)
    combined=[];last_offset=None;last_end=None
    for index,page in enumerate(pages):
        if (page.get('ok') is not True or page.get('read_only') is not True or page.get('scope')!='member_visual'
                or page.get('final_invite_clicked') is not False or page.get('header_page') is not True):
            raise ValueError('滚动页不是有效只读证据')
        if any(page.get(key)!=reference.get(key) for key in BIND_KEYS):raise ValueError('滚动页弹窗身份改变')
        inner,view,offset,max_offset=geometry(page)
        if (view!=rv or inner[3]!=ri[3] or max_offset!=maximum
                or any(page['header_scroll'][key]!=reference['header_scroll'][key] for key in CONTROL_KEYS)):
            raise ValueError('滚动期间已选内容、搜索值或控件改变')
        if page['regions'].get('header_ocr')!=page_area(page):raise ValueError('滚动页文字范围未正确裁剪')
        area=rect(page['regions']['header_ocr'])
        start=offset+area[1]-view[1];end=start+area[3]
        if index==0 and offset!=0:raise ValueError('滚动读取未从顶部开始')
        if last_offset is not None and (offset<=last_offset or last_end-start<MAX_LABEL_HEIGHT):
            raise ValueError('滚动页未递增或重叠不足，不能完整核对')
        last_offset=offset;last_end=end
        for label,bounds in observations(page):
            matches=[row for row in combined if same_place(row[1],bounds)]
            if matches:
                if len(matches)!=1 or matches[0][0]!=label:raise ValueError('重叠页同一位置的标签识别冲突')
            else:combined.append((label,bounds))
    if last_offset!=maximum or last_end!=ri[3]:raise ValueError('滚动读取未覆盖到底部')
    lines=[]
    for row in sorted(combined,key=lambda row:row[1][1]+row[1][3]/2):
        if lines and abs(row[1][1]+row[1][3]/2-lines[-1][0][1][1]-lines[-1][0][1][3]/2)<=8:
            lines[-1].append(row)
        else:lines.append([row])
    combined=[row for line in lines for row in sorted(line,key=lambda row:row[1][0])]
    # Preserve equal labels at separate positions, so duplicate chips remain ambiguous.
    return combined


def validate_header_point(probe,report):
    """Require an exact bound selected-header control, never just its modal parent."""
    _,view,offset,maximum=geometry(report)
    if probe.get('verified') is not True or probe.get('search_excluded') is not True:
        raise ValueError('滚轮命中点未核对')
    x=float(probe['x']);y=float(probe['y']);before=float(probe['offset'])
    if not all(math.isfinite(v) for v in (x,y,before)) or not 0<=before<=maximum:
        raise ValueError('滚轮命中点坐标无效')
    search=rect(report['regions']['search'])
    search=(search[0],search[1]+offset-before,search[2],search[3])
    if rect(probe['viewport'])!=view or rect(probe['search'])!=search:
        raise ValueError('滚轮命中点布局证据不一致')
    if not view[0]<x<view[0]+view[2] or not view[1]<y<view[1]+view[3]:
        raise ValueError('滚轮命中点超出可见区域')
    if search[0]<=x<search[0]+search[2] and search[1]<=y<search[1]+search[3]:
        raise ValueError('滚轮命中点位于搜索输入框')
    state=report['header_scroll']
    allowed={state['inner_runtime_id']:'class Ui::MultiSelect::Inner',
        state['viewport_runtime_id']:'class QWidget',state['scroll_runtime_id']:'class Ui::ScrollArea'}
    target=probe.get('bound_runtime_id');kind=probe.get('bound_class')
    if not target or allowed.get(target)!=kind:raise ValueError('滚轮命中点不是绑定的顶部组件')
    paths=probe.get('paths')
    if not isinstance(paths,list) or not 1<=len(paths)<=2:raise ValueError('缺少命中点实际控件路径')
    if probe.get('mode')=='qt_modal_surface':
        return validate_qt_modal_surface(probe,report,view,before,search)
    for path in paths:
        if path.get('walker') not in ('raw','control') or path.get('error'):continue
        nodes=path.get('nodes')
        if not isinstance(nodes,list) or not 1<=len(nodes)<=24:continue
        for node in nodes:
            if node.get('process_id')!=report['process_id']:break
            if node.get('runtime_id')==target and node.get('class_name')==kind:
                bounds=rect(node['bounds'])
                if not bounds[0]<=x<bounds[0]+bounds[2] or not bounds[1]<=y<bounds[1]+bounds[3]:break
                if target==state['inner_runtime_id']:
                    expected=(view[0],view[1]-before,view[2],view[3]+maximum)
                else:expected=view
                if bounds!=expected:break
                return True
    raise ValueError('命中点控件路径未绑定当前顶部组件')


def bound_history_path(nodes, ancestors, pid):
    """Match the actual history ancestor chain even when the point hits its child."""
    if not ancestors:return False
    starts=[i for i,n in enumerate(nodes) if n.get('runtime_id')==ancestors[0].get('runtime_id')]
    if len(starts)!=1:return False
    start=starts[0]
    if start>6 or len(nodes)-start<len(ancestors):return False
    if any(n.get('process_id')!=pid or not n.get('runtime_id') for n in nodes[:start]):return False
    return all(all(n.get(k)==a.get(k) for k in ('runtime_id','class_name','process_id'))
               for n,a in zip(nodes[start:],ancestors))


def validate_qt_modal_surface(probe,report,view,before,search):
    """Observed Qt point lookup sees HistoryInner behind the known modal.

    Accept only that exact bound branch plus a fresh visible-screen comparison;
    the history widget itself is never considered the selected-header target.
    """
    import re
    if probe.get('geometry_bound') is not True:raise ValueError('Qt 弹窗可见范围未绑定')
    surface=probe.get('surface') or {}
    if surface.get('ok') is not True or surface.get('foreground_verified') is not True:
        raise ValueError('屏幕上实际显示的顶部区域未核对')
    frame=surface.get('frame') or {};state=frame.get('header_scroll') or {}
    if (any(frame.get(k)!=report.get(k) for k in BIND_KEYS[:4]) or state.get('offset')!=before
            or any(state.get(k)!=report['header_scroll'].get(k) for k in CONTROL_KEYS)
            or state.get('max_offset')!=report['header_scroll'].get('max_offset')
            or rect(state['viewport'])!=view or rect(surface['header'])!=view or rect(surface['search'])!=search):
        raise ValueError('屏幕核对与当前弹窗或滚动位置不一致')
    inner=rect(state['inner'])
    if inner!=(view[0],view[1]-before,view[2],view[3]+report['header_scroll']['max_offset']):
        raise ValueError('屏幕核对内容区域不一致')
    compared=surface.get('compared_pixels');different=surface.get('different_pixels')
    if (type(compared) is not int or type(different) is not int or compared<1000
            or compared>view[2]*view[3]*4 or not 0<=different<=compared or different/compared>0.005):
        raise ValueError('屏幕与弹窗顶部图像不一致')
    for key in ('reference_sha256','screen_sha256'):
        if not isinstance(surface.get(key),str) or not re.fullmatch('[0-9a-f]{64}',surface[key]):
            raise ValueError('缺少屏幕核对截图摘要')
    attempts=probe.get('surface_attempts')
    if not isinstance(attempts,list) or not 1<=len(attempts)<=3 or attempts[-1]!=surface:
        raise ValueError('屏幕稳定核对缺少最终通过的完整记录')
    paths=[]
    for index,attempt in enumerate(attempts,1):
        ac=attempt.get('compared_pixels');ad=attempt.get('different_pixels')
        if (attempt.get('attempt_index')!=index or attempt.get('foreground_verified') is not True
                or attempt.get('frame')!=frame or rect(attempt['header'])!=view or rect(attempt['search'])!=search
                or type(ac) is not int or type(ad) is not int or ac!=compared or not 0<=ad<=ac
                or attempt.get('ok') is not (index==len(attempts))
                or (ad/ac<=0.005)!=(index==len(attempts))):
            raise ValueError('屏幕稳定核对期间窗口、位置或核对结果改变')
        for path_key,hash_key in (('reference_image_path','reference_sha256'),('screen_image_path','screen_sha256')):
            if (not isinstance(attempt.get(path_key),str) or not attempt[path_key]
                    or not isinstance(attempt.get(hash_key),str) or not re.fullmatch('[0-9a-f]{64}',attempt[hash_key])):
                raise ValueError('屏幕稳定核对截图记录不完整')
            paths.append(attempt[path_key])
    if len(set(paths))!=len(paths):raise ValueError('屏幕稳定核对截图路径重复')
    binding=probe.get('qt_history_binding') or {};controls=binding.get('controls')
    expected=('class HistoryInner','class Ui::ElasticScroll','class HistoryWidget')
    if not isinstance(controls,list) or len(controls)!=3 or tuple(c.get('class_name') for c in controls)!=expected:
        raise ValueError('不是现场确认的 Qt 聊天控件分支')
    if not binding.get('main_runtime_id') or any(not c.get('runtime_id') for c in controls):
        raise ValueError('缺少 Qt 原窗口分支身份')
    ancestors=binding.get('ancestor_nodes')
    if binding.get('binding_method')!='history_ancestors' or not isinstance(ancestors,list) or not 4<=len(ancestors)<=24:
        raise ValueError('Qt 聊天分支必须沿实际父级绑定')
    ids=[n.get('runtime_id') for n in ancestors]
    if (any(not isinstance(rid,str) or not rid for rid in ids) or len(set(ids))!=len(ids)
            or any(n.get('process_id')!=report['process_id'] or not n.get('class_name') for n in ancestors)
            or any(ancestors[i].get('runtime_id')!=controls[i]['runtime_id'] or
                ancestors[i].get('class_name')!=expected[i] for i in range(3))
            or ids[-1]!=binding['main_runtime_id'] or ancestors[-1].get('class_name')!='class MainWindow'):
        raise ValueError('Qt 父级链未绑定原聊天控件和原窗口')
    for path in probe['paths']:
        nodes=path.get('nodes') or []
        if path.get('walker')!='raw' or path.get('error') or len(nodes)<len(ancestors):continue
        if bound_history_path(nodes,ancestors,report['process_id']):
            return True
    raise ValueError('Qt 命中路径未绑定原窗口，不能使用屏幕核对')


def verify_native_surface_files(native):
    """Bind fresh screen/reference files before any caller can trust full OCR."""
    for probe in native.get('surface_checks',[]):
        surface=probe.get('surface') or {}
        if surface.get('ok') is not True:raise ValueError('有未通过的屏幕核对')
        for attempt in probe.get('surface_attempts',[surface]):
            for path_key,digest_key in (('reference_image_path','reference_sha256'),('screen_image_path','screen_sha256')):
                if hashlib.sha256(Path(attempt[path_key]).read_bytes()).hexdigest()!=attempt[digest_key]:
                    raise ValueError('屏幕核对截图文件改变')


def validate_full_header(report):
    proof=report['full_header_scan']
    if (proof.get('ok') is not True or proof.get('scope')!='full_current_header'
            or proof.get('selection_click_sent') is not False or proof.get('search_changed') is not False
            or proof.get('final_invite_clicked') is not False or proof.get('database_updated') is not False):
        raise ValueError('完整已选区域核对失败：'+str(proof.get('error') or '证据不完整'))
    for key in BIND_KEYS+('regions','header_scroll'):
        if proof.get('final_frame',{}).get(key)!=report.get(key):raise ValueError('完整区域证据与当前截图不一致')
    if proof.get('final_image_sha256')!=report['selection_header_ocr']['image_sha256']:
        raise ValueError('完整区域最终截图校验不一致')
    window=proof['window']
    if window['hwnd']!=report['window_handle'] or window['pid']!=report['process_id']:
        raise ValueError('完整区域所属窗口错误')
    native=proof['native']
    if (native.get('ok') is not True or native.get('scope')!='member_header_scroll_scan'
            or native.get('process_path_verified') is not True or native.get('header_binding_verified') is not True
            or native.get('executable_path')!=window['path'] or native.get('state')!='captured'
            or native.get('point_guard_version')!=4
            or any(native.get(key)!=report.get(key) for key in BIND_KEYS[:3])
            or any(native.get(key) is not False for key in ('search_changed','selection_click_sent','final_invite_clicked','database_updated'))):
        raise ValueError('原生滚动过程绑定不完整')
    _,view,offset,maximum=geometry(report)
    if offset!=maximum:raise ValueError('完整核对后未回到搜索框所在的底部')
    events=native.get('wheel_events')
    if not isinstance(events,list) or not 1<=len(events)<=96:raise ValueError('缺少滚动动作记录')
    downward=False
    single_down=native.get('scan_mode')=='single_downward_page_scan'
    for index,event in enumerate(events,1):
        if (event.get('index')!=index or event.get('requested') is not True or event.get('sent') is not True
                or event.get('binding_verified') is not True
                or event.get('viewport_runtime_id')!=report['header_scroll']['viewport_runtime_id']
                or event.get('delta') not in ((-120,-40,1200) if single_down else (-40,40))
                or not view[0]<event['x']<view[0]+view[2] or not view[1]<event['y']<view[1]+view[3]):
            raise ValueError('滚轮动作或可见区域证据不完整')
        if single_down:
            phase=event.get('phase');delta=event.get('delta')
            if phase=='position_top':
                if downward or delta!=1200:raise ValueError('读取开始后不能再次向上滚动')
            elif phase=='read_down':
                downward=True
                if delta not in (-120,-40) or float(event['after_offset'])-float(event['before_offset'])>view[3]-44:
                    raise ValueError('向下滚动步长或页面重叠不足')
            else:raise ValueError('缺少单向滚动阶段标记')
        point=event.get('point_proof') or {}
        if point.get('x')!=event['x'] or point.get('y')!=event['y'] or point.get('offset')!=event['before_offset']:
            raise ValueError('滚轮动作与命中点证据不一致')
        if point.get('mode')=='qt_modal_surface' and point.get('qt_history_binding')!=native.get('qt_history_binding'):
            raise ValueError('Qt 备用定位路径与本次窗口绑定不一致')
        validate_header_point(point,report)
        if event.get('pointer_parked') is not True:raise ValueError('读取前鼠标未离开联系人标签')
        before=float(event['before_offset']);after=float(event['after_offset'])
        if not 0<=before<=maximum or not 0<=after<=maximum or (after-before)*event['delta']>0:
            raise ValueError('滚轮结果与方向不一致')
    passes=proof['passes']
    count=proof.get('scan_pass_count',2)
    if single_down and (count!=1 or not downward):raise ValueError('单向读取必须只有一轮向下扫描')
    if count not in (1,2) or native.get('scan_pass_count',2)!=count:
        raise ValueError('完整读取轮数绑定不一致')
    if not isinstance(passes,list) or len(passes)!=count:raise ValueError('完整读取轮数不一致')
    rows=[validate_pass(pages,report) for pages in passes]
    a=rows[0]
    if count==2:
        b=rows[1]
        if len(a)!=len(b) or any(len([y for y in b if x[0]==y[0] and same_place(x[1],y[1])])!=1 for x in a):
            raise ValueError('两轮完整已选标签读取不一致')
    for label,bounds in observations(report):
        matches=[row for row in a if same_place(row[1],bounds)]
        if len(matches)!=1 or matches[0][0]!=label:raise ValueError('最终截图与完整标签名单不一致')
    from visual_members import numeric_word
    return [label for label,_ in a if numeric_word(label)],[label for label,_ in a if not numeric_word(label)]


def read_current_header(window,report):
    """Return a fresh bottom capture plus one fully bound live header pass."""
    from controls_probe import run_window_script, WindowActionError
    from backup_ocr import apply_selection_header, run_backup_ocr, save_report, require_environment
    state=report.get('header_scroll')
    if not state or float(state.get('max_offset',0))<=0:return apply_selection_header(report)
    failed=copy.deepcopy(report)
    failed['full_header_scan']={'ok':False,'scope':'full_current_header'}
    try:
        require_environment()
        geometry(report)
        prefix=str(Path(report['image_path']).with_suffix(''))+'_header_scan'
        native=run_window_script(window,'scroll_member_header.ps1',{'source':report,'prefix':prefix,'executable_path':window['path']})
        if native.get('ok') is not True:raise ValueError('原生滚动读取未完成')
        verify_native_surface_files(native)
        pages=[page for group in native['passes'] for page in group['pages']]+[native['final']]
        worker_report=copy.deepcopy(native['final'])
        prepared=[];digests=[]
        for page in pages:
            page=copy.deepcopy(page);page['header_page']=True;page['header_scan_only']=True;page['regions']['header_ocr']=page_area(page)
            prepared.append(page);digests.append(hashlib.sha256(Path(page['image_path']).read_bytes()).hexdigest())
        worker_report['header_scan_pages']=prepared
        secondary=run_backup_ocr(worker_report)
        if secondary.get('ok') is not True or len(secondary.get('pages',[]))!=len(prepared):
            raise ValueError(secondary.get('error','滚动页备用识别未完成'))
        bound=[]
        for page,backup,digest in zip(prepared,secondary['pages'],digests):
            if backup.get('ok') is not True or backup.get('image_sha256')!=digest or hashlib.sha256(Path(page['image_path']).read_bytes()).hexdigest()!=digest:
                raise ValueError('滚动页截图已改变或识别绑定失败')
            bound.append(bind_page(page,backup,digest))
        groups=[];position=0
        for group in native['passes']:
            count=len(group['pages']);groups.append(bound[position:position+count]);position+=count
        if position!=len(bound)-1:raise ValueError('滚动页数量不一致')
        result=bound[-1]
        if 'search_preparation' in report:result['search_preparation']=copy.deepcopy(report['search_preparation'])
        result['full_header_scan']={'ok':True,'scope':'full_current_header','window':copy.deepcopy(window),
            'native':{k:copy.deepcopy(v) for k,v in native.items() if k not in ('passes','final')},
            'passes':groups,'scan_pass_count':native.get('scan_pass_count',2),
            'final_frame':{k:copy.deepcopy(result[k]) for k in BIND_KEYS+('regions','header_scroll')},
            'final_image_sha256':digests[-1],
            'search_changed':False,'selection_click_sent':False,'final_invite_clicked':False,'database_updated':False}
        validate_full_header(result)
        from visual_members import analyze_member_visual
        result['analysis']=analyze_member_visual(result,'1')
        if not result['analysis']['usable']:raise ValueError(result['analysis']['reason'])
        save_report(result,result['image_path'])
        return result
    except Exception as error:
        if isinstance(error,WindowActionError):failed['full_header_scan']['native_error']=error.report
        failed['full_header_scan']['error']=str(error)
        failed['selection_header_ocr']={'ok':False,'used_for_selection':False,'error':str(error)}
        return failed
