"""Run existing member selection in explicit window order; never submit invites."""
from collections import Counter
import copy
import json
from pathlib import Path
import uuid

from member_batch import select_members_test
from visual_members import member_labels
from windows_scan import is_telegram_process


def queue_windows(windows):
    rows=copy.deepcopy(list(windows))
    if not 2<=len(rows)<=80:raise ValueError('窗口顺序测试请选择 2–80 个窗口；本轮先用两个')
    seen=set()
    for row in rows:
        if (not isinstance(row,dict) or type(row.get('hwnd')) is not int or row['hwnd']<=0
                or type(row.get('pid')) is not int or row['pid']<=0
                or not is_telegram_process(row.get('path')) or row.get('candidate') is not True
                or row.get('minimized') is not False):
            raise ValueError('每个窗口必须是已还原、路径可读取的 Telegram.exe 候选窗口')
        if row['hwnd'] in seen:raise ValueError('窗口重复，未开始操作')
        seen.add(row['hwnd'])
    return rows


def ready_for_manual_invite(result,window,labels):
    final=result.get('final') or {}
    ready=(result.get('ok') is True and result.get('scope')=='multi_member_selection'
        and result.get('selection_verified') is True and result.get('state')=='waiting_for_manual_invite'
        and result.get('final_invite_clicked') is False and result.get('database_updated') is False
        and Counter(result.get('numbers',[]))==Counter(labels)
        and Counter(result.get('selected_numbers',[]))==Counter(labels)
        and result.get('remaining_numbers')==[]
        and final.get('ok') is True and final.get('read_only') is True and final.get('scope')=='member_visual'
        and final.get('window_handle')==window['hwnd'] and final.get('process_id')==window['pid']
        and bool(final.get('dialog_runtime_id')) and final.get('final_invite_clicked') is False)
    if not ready or result.get('verification_mode')!='target_only_each_step':return ready
    # Completion is the accumulated per-target evidence, not invitation proof.
    from visual_members import verify_target_member_selection
    steps=result.get('steps') or []
    if len(steps)!=len(labels) or (result.get('initial',{}).get('analysis') or {}).get('selected_numbers')!=[]:
        return False
    try:
        for i,(step,name) in enumerate(zip(steps,labels)):
            before=step['before'];after=step['after']
            if (step.get('number')!=name or step.get('selection_verified') is not True
                    or step.get('click_sent') is not True or step.get('search_applied') is not True
                    or step.get('expected_selected')!=list(labels[:i])
                    or before.get('window_handle')!=window['hwnd'] or before.get('process_id')!=window['pid']
                    or after.get('dialog_runtime_id')!=final['dialog_runtime_id']
                    or not verify_target_member_selection(before,after,name,labels[:i])['selection_verified']):
                return False
        return final==steps[-1]['after']
    except (KeyError,TypeError,ValueError):return False


def select_window_queue(windows,report_path,numbers=None,progress=None,*,numbers_by_window=None):
    rows=queue_windows(windows)
    if numbers_by_window is not None:
        if numbers is not None or len(numbers_by_window)!=len(rows):raise ValueError('每个窗口需要各自一份备注名单')
        lists=[member_labels(values) for values in numbers_by_window]
    else:
        labels=member_labels(numbers);lists=[labels[:] for row in rows]
    path=Path(report_path).resolve()
    if path.suffix.lower()!='.json':raise ValueError('窗口顺序汇总请保存为 JSON')
    files=path.parent/(path.stem+'_windows_'+uuid.uuid4().hex[:8])
    jobs=[{'index':i,'window':row,'numbers':labels[:],'state':'not_started',
        'report_path':str(files/f'window{i:02d}.json')} for i,(row,labels) in enumerate(zip(rows,lists),1)]
    result={'ok':False,'scope':'multi_window_selection','state':'preparing','numbers_by_window':copy.deepcopy(lists),
        'verified_accounts':False,'selection_verified':False,'final_invite_clicked':False,
        'database_updated':False,'waiting_windows':[],'jobs':jobs,
        'report_path':str(path),'files_directory':str(files)}
    def save():
        temporary=path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)
    def announce(message):
        if progress is not None:progress(message)
    save()  # The complete explicit order is durable before any UI action.
    current=None
    try:
        files.mkdir()
        for job in jobs:
            current=job
            labels=job['numbers']
            result.update(state='executing',current_index=job['index'])
            job['state']='running';save()
            prefix=f'窗口 {job["index"]}/{len(jobs)}（0x{job["window"]["hwnd"]:X}）'
            announce(prefix+'：开始核对与选择；前一窗口保留在 Add 前。')
            child=select_members_test(copy.deepcopy(job['window']),job['report_path'],labels[:],
                lambda message,prefix=prefix:announce(prefix+'：'+message))
            job['result']={key:copy.deepcopy(child.get(key)) for key in
                ('ok','scope','state','selection_verified','numbers','selected_numbers','remaining_numbers',
                 'final_invite_clicked','database_updated','reason','report_path','final_image_path')}
            if not ready_for_manual_invite(child,job['window'],labels):
                job['state']='review'
                result.update(state='review',reason=prefix+' 未完整核对，后续窗口未执行：'+child.get('reason','请检查对应窗口'))
                save();return result
            job.update(state='waiting_for_manual_invite',final_image_path=child.get('final_image_path'))
            result['waiting_windows'].append(job['window']['hwnd'])
            save()  # Preserve A's completed selection before starting B, no human wait.
        result.update(ok=True,state='waiting_for_manual_invite',selection_verified=True,
            reason='所选窗口均已核对并停在 Add 前；请逐个窗口手动邀请')
        save();return result
    except Exception as error:
        if current is not None and current['state']=='running':current['state']='review'
        result.update(state='review',reason=str(error)+'；队列停止，后续窗口未执行，已核对窗口的选择保留。')
        save();return result
