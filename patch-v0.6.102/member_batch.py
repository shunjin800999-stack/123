"""Select an explicit local list in one existing Add Members dialog. Never invite."""
from collections import Counter
import json
from pathlib import Path
import uuid
import time
import copy

from controls_probe import inspect_member_search, select_member_test, WindowActionError, check_member_pause, MemberSelectionPaused
from backup_ocr import inspect_selection_visual as inspect_member_visual, apply_selection_header
from visual_members import member_labels
from selection_header_scan import read_current_header


def inspect_final_member(window,image_path,labels):
    from controls_probe import inspect_member_visual as capture_member_visual
    from backup_ocr import apply_visible_header
    from visual_members import analyze_member_visual
    report=capture_member_visual(window,image_path,labels[-1])
    report=apply_visible_header(report,labels[:-1],labels[-1],'after')
    report['analysis']=analyze_member_visual(report,labels[-1])
    return report


def inspect_initial_members(window, image_path, number, on_search_requested=None, *, expected_dialog_id=None):
    """Read existing selections first; initial OCR ambiguity never permits a click."""
    path=Path(image_path)
    reports=[];searched=False;search_report=None
    for attempt in range(1,4):
        time.sleep(0.5)
        image=path if attempt==3 else path.with_name(path.stem+f'_check{attempt}.png')
        report=inspect_member_visual(window,image,number)
        reports.append(report)
        if expected_dialog_id is not None and (report.get('dialog_runtime_id')!=expected_dialog_id
                or report.get('window_handle')!=window.get('hwnd')
                or report.get('process_id')!=window.get('pid')):
            raise ValueError('当前添加成员弹窗与刚打开的目标群不一致，未搜索或选人')
        first=reports[0]
        if any(first.get(key) is None or first.get(key)!=report.get(key) for key in
               ('window_handle','process_id','dialog_runtime_id','capture')):
            raise ValueError('初始复查期间窗口或弹窗改变，停止核查')
        a=report['analysis']
        if attempt==1 and a['usable'] and not a['selected_numbers'] and a['other_header_words']==['p']:
            # Only the observed empty-search magnifier case prepares a query.
            # Never ignore p/0/punctuation or guess which people are selected.
            if on_search_requested is not None:on_search_requested()
            search_guard={} if expected_dialog_id is None else {'expected_dialog_id':expected_dialog_id}
            search_report=inspect_member_search(window,path.with_name(path.stem+'_search.png'),number,**search_guard)
            if any(first.get(key)!=search_report.get(key) for key in
                   ('window_handle','process_id','dialog_runtime_id','capture')):
                raise ValueError('初始搜索期间窗口或弹窗改变，停止核查')
            searched=True
    a,b=(report['analysis'] for report in reports[-2:])
    stable=(a['usable'] and b['usable'] and not a['other_header_words'] and not b['other_header_words']
        and Counter(a['selected_numbers'])==Counter(b['selected_numbers'])
        and all(report.get('final_invite_clicked') is False for report in reports[-2:]))
    result=copy.deepcopy(reports[-1])
    result.update(initial_stable=bool(stable),initial_search_applied=searched,
        initial_reads=[{'attempt':i,'report':report} for i,report in enumerate(reports,1)],
        initial_search_report=search_report)
    return result


def select_members_test(window, report_path, numbers, progress=None, *, expected_dialog_id=None, fast_visible=False, should_stop=None):
    labels=member_labels(numbers)
    path=Path(report_path).resolve()
    if path.suffix.lower()!='.json':raise ValueError('连续选人汇总请保存为 JSON')
    files=path.parent/(path.stem+'_files_'+uuid.uuid4().hex[:8])
    result={'ok':False,'scope':'multi_member_selection','read_only':False,
        'state':'preparing','numbers':labels,'selected_numbers':[],'remaining_numbers':labels[:],
        'selection_verified':False,'final_invite_clicked':False,'database_updated':False,
        'initial_search_requested':False,'initial_search_applied':False,
        'report_path':str(path),'files_directory':str(files),'steps':[]}
    def save():
        temporary=path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)
    def announce(text):
        if progress is not None:progress(text)
    save()  # All input validation and a pending summary precede any window action.
    try:
        check_member_pause(should_stop)
        files.mkdir()
        announce('正在只读复查当前已选名单；最后两次一致才继续，不点击最终 Add。')
        def search_pending():
            result['initial_search_requested']=True;save()
        guard={} if expected_dialog_id is None else {'expected_dialog_id':expected_dialog_id}
        if fast_visible:
            from backup_ocr import apply_visible_header
            search_pending()
            raw=inspect_member_search(window,files/'initial.png',labels[0],**guard)
            initial=apply_visible_header(raw,[],labels[0],'before')
            from visual_members import analyze_member_visual
            initial['analysis']=analyze_member_visual(initial,labels[0])
            initial.update(initial_stable=initial['analysis']['usable'] and not initial['analysis']['selected_numbers'],
                initial_search_applied=True,initial_reads=[{'attempt':1,'report':copy.deepcopy(initial)}])
        else:
            initial=inspect_initial_members(window,files/'initial.png',labels[0],search_pending,**guard)
        if expected_dialog_id is not None and initial.get('dialog_runtime_id')!=expected_dialog_id:
            raise ValueError('初始弹窗不属于刚打开的目标群，未选人')
        result['initial']=initial
        result['initial_search_applied']=initial['initial_search_applied']
        result['initial_reads']=initial['initial_reads']
        a=initial['analysis']
        if not initial['initial_stable']:
            if fast_visible and not a['usable']:
                raise ValueError('初始只读识别未通过：'+a['reason'])
            raise ValueError('初始已选名单未连续两次完整一致，停止；请保留已选联系人并检查截图')
        selected=member_labels(a['selected_numbers'],allow_empty=True)
        if not set(selected)<=set(labels):raise ValueError('顶部已有本次列表之外的备注；停止，不取消任何人')
        result['selected_numbers']=selected[:]
        result['remaining_numbers']=[label for label in labels if label not in selected]
        save()
        def header_reader(report):
            state=report.get('header_scroll') or {}
            return read_current_header(window,report) if float(state.get('max_offset',0))>0 else apply_selection_header(report)
        for index,label in enumerate(labels,1):
            check_member_pause(should_stop)
            if label in selected:
                result['steps'].append({'number':label,'state':'already_selected','selection_verified':True})
                save();continue
            result.update(state='selecting',current_number=label)
            save()
            announce(f'连续选人：第 {index}/{len(labels)} 位，备注 {label}；已核对 {len(selected)} 位。')
            options={'fast_visible':True,'should_stop':should_stop} if fast_visible else {}
            if should_stop is not None:options['should_stop']=should_stop
            step=select_member_test(window,files/f'step{index:02d}_{label}.png',label,
                expected_selected=tuple(selected),expected_dialog=initial,header_reader=header_reader,**options)
            result['steps'].append(step)
            if step.get('selection_verified') is not True:
                result.update(state='paused' if step.get('state')=='paused' else 'review',reason=f'备注 {label} 未核对通过，后续未执行：'+step.get('reason','请查看页面'))
                save();return result
            selected.append(label)
            result['selected_numbers']=selected[:]
            result['remaining_numbers']=[value for value in labels if value not in selected]
            save()
        announce('逐位选择结束，正在核对最后一位备注。' if fast_visible else '逐位选择结束，正在再次核对全部已选备注。')
        result['state']='verifying_last' if fast_visible else 'verifying_all';save()
        result['final_reads']=[];consecutive=0
        for attempt in (range(1,3) if fast_visible else range(1,4)):
            check_member_pause(should_stop)
            time.sleep(0.2 if fast_visible else 0.5)
            image=files/('final.png' if fast_visible or attempt==3 else f'final_check{attempt}.png')
            final=inspect_final_member(window,image,labels) if fast_visible else inspect_member_visual(window,image,labels[0])
            result['final']=final;result['final_image_path']=final['image_path']
            b=final['analysis']
            identity=all(initial.get(key) is not None and initial.get(key)==final.get(key)
                         for key in ('window_handle','process_id','dialog_runtime_id','capture'))
            if fast_visible:
                checkpoint={'scope':'visible_pair','ledger':labels[:-1],'number':labels[-1],'stage':'after'}
                matched=(identity and final.get('visible_header_checkpoint')==checkpoint
                    and not final.get('full_header_scan') and b['usable'] and not b['other_header_words']
                    and b['selected_numbers'].count(labels[-1])==1
                    and set(b['selected_numbers'])<=set(labels) and final.get('final_invite_clicked') is False)
            else:
                matched=(identity and not final.get('visible_header_checkpoint') and b['usable'] and not b['other_header_words']
                    and Counter(b['selected_numbers'])==Counter(labels) and final.get('final_invite_clicked') is False)
            result['final_reads'].append({'attempt':attempt,'report':final,'selection_verified':bool(matched)})
            save()
            if not identity:raise ValueError('最终复查期间窗口或弹窗改变，停止核查')
            if b['other_header_words']:raise ValueError('最终复查发现其他文字备注，停止核查')
            consecutive=consecutive+1 if matched else 0
        verified=consecutive>=2
        result['verification_mode']='visible_pair_then_last_final' if fast_visible else 'complete_each_step'
        result['selection_verified']=bool(verified);result['ok']=bool(verified)
        result['state']='waiting_for_manual_invite' if verified else 'review'
        result['reason']=('逐位选择完成，最后备注已核对，停在 Add 前' if fast_visible else '全部备注已选中并核对，停在 Add 前') if verified else '最终备注未核对通过；停止，请查看截图，不自动重试'
        save();return result
    except Exception as error:
        if isinstance(error,WindowActionError):result['action_error']=error.report
        result.update(state='paused' if (isinstance(error,MemberSelectionPaused) or (should_stop is not None and should_stop())) else 'review',reason=str(error)+'；后续未执行，也未点击最终 Add。')
        save();return result
