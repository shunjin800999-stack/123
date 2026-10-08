"""Open a frozen target in each window, never select contacts or submit invites."""
import copy
import json
from pathlib import Path
import time

from controls_probe import run_window_script, WindowActionError
from groups import valid_window
from pinned_groups import consistent_pair


def navigation_entries(observations):
    entries=copy.deepcopy(list(observations))
    if not 1<=len(entries)<=80:raise ValueError('请选择 1–80 个已有置顶群记录的窗口，本轮先测试一个')
    accounts=set();windows=set()
    for record in entries:
        if (not isinstance(record,dict) or record.get('scope')!='pinned_group_observation'
                or not valid_window(record.get('window')) or not isinstance(record.get('account'),str)
                or not record['account'].strip() or not record.get('list_runtime_id')):
            raise ValueError('窗口缺少已保存的置顶群记录，请先重新识别')
        if record['account'] in accounts or record['window']['hwnd'] in windows:
            raise ValueError('账号或窗口重复，未开始打开群')
        accounts.add(record['account']);windows.add(record['window']['hwnd'])
        targets=record.get('targets')
        if not isinstance(targets,list) or len(targets)!=2:raise ValueError('每个账号需要两个置顶群记录')
        for slot,target in enumerate(targets,1):
            if (not isinstance(target,dict) or type(target.get('slot')) is not int or target['slot']!=slot
                    or not target.get('name') or not target.get('runtime_id')
                    or target.get('language') not in ('en','pt')):
                raise ValueError('置顶群记录无效，请重新识别')
        if targets[0]['runtime_id']==targets[1]['runtime_id']:raise ValueError('两个群不能指向同一个控件')
    return entries


def same_observed_targets(record, pair):
    return (record['list_runtime_id']==pair['list_runtime_id'] and
        all(all(a.get(k)==b.get(k) for k in ('slot','name','language','runtime_id','group_label','pinned_label'))
            for a,b in zip(record['targets'],pair['targets'])))


def navigation_verified(report, record, slot):
    target=record['targets'][slot-1];window=record['window']
    if (not isinstance(report,dict) or report.get('ok') is not True
            or report.get('scope')!='pinned_group_navigation' or report.get('state')!='members_open'
            or report.get('window_handle')!=window['hwnd'] or report.get('process_id')!=window['pid']
            or report.get('slot')!=slot or report.get('group_name')!=target['name']
            or report.get('group_runtime_id')!=target['runtime_id']
            or report.get('errors')!=[]
            or report.get('profile_verified') is not True or not report.get('profile_runtime_id')
            or not report.get('dialog_runtime_id') or report.get('final_invite_clicked') is not False
            or report.get('members_selected') is not False or report.get('contact_database_updated') is not False):
        return False
    actions=report.get('actions')
    return (isinstance(actions,list) and all(isinstance(a,dict) for a in actions) and [a.get('action') for a in actions]==
        ['open_chat','open_info','open_members'] and all(a.get('attempted') is True and a.get('invoked') is True for a in actions))


def open_pinned_groups(observations, slot, report_path, progress=None):
    if type(slot) is not int or slot not in (1,2):raise ValueError('目标群只能选择 1 或 2')
    entries=navigation_entries(observations)
    path=Path(report_path).resolve()
    if path.suffix.lower()!='.json':raise ValueError('群打开报告请保存为 JSON')
    jobs=[{'index':i,'account':r['account'],'window':copy.deepcopy(r['window']),
        'slot':slot,'target':copy.deepcopy(r['targets'][slot-1]),'state':'not_started'} for i,r in enumerate(entries,1)]
    result={'ok':False,'scope':'pinned_group_navigation_queue','state':'preparing','slot':slot,
        'jobs':jobs,'report_path':str(path),'members_selected':False,'final_invite_clicked':False,
        'contact_database_updated':False,'account_identity_verified':False,'reason':''}
    def save():
        temp=path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    save() # Freeze the complete window order and targets before navigating.
    current=None
    try:
        for record,job in zip(entries,jobs):
            current=job;job['state']='reading';result['state']='running';save()
            if progress:progress(f'{job["account"]}：核对群 {slot}「{job["target"]["name"]}」')
            first=run_window_script(record['window'],'inspect_groups.ps1')
            time.sleep(0.5)
            second=run_window_script(record['window'],'inspect_groups.ps1')
            pair=consistent_pair(first,second,record['window'])
            if not same_observed_targets(record,pair):
                raise ValueError(f'{job["account"]} 的置顶群、顺序或控件标识已变化，请重新识别并记录；未打开该群')
            job['state']='opening';save()
            child=run_window_script(record['window'],'navigate_group.ps1',{
                'slot':slot,'list_runtime_id':pair['list_runtime_id'],'targets':pair['targets']})
            job['navigation']=child
            if not navigation_verified(child,record,slot):raise ValueError('群打开结果未完整核对，停止后续窗口')
            # A remains at Add members while B begins; no human wait and no
            # global numbering, contact result or invite result is updated.
            job['state']='members_open';save()
            if progress:progress(f'{job["account"]}：群 {slot} 添加成员页面已打开，未选人；继续下一窗口')
        result.update(ok=True,state='members_open',reason='所选账号的目标群添加成员页面均已打开并核对；未选人或邀请。')
        save();return result
    except Exception as error:
        if current:
            current['state']='review'
            if isinstance(error,WindowActionError):current['navigation']=error.report
        result.update(state='review',reason=str(error)+'；已打开窗口保留，后续窗口未执行，不自动重试。')
        save();return result
