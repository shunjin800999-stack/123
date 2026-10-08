"""Bound page preparation. No selection, Create or final invitation actions."""
import copy
import hashlib
import json
import math
from pathlib import Path

from controls_probe import run_window_script,WindowActionError
from pinned_scan import path_key


def valid_area(area):
    return (isinstance(area,dict) and all(type(area.get(k)) in (int,float) and math.isfinite(area[k])
        for k in ('left','top','width','height')) and area['width']>0 and area['height']>0)


def target_visibility_verified(report,record):
    """Qt offscreen flags alone include rows beyond the scroll viewport."""
    proof=report.get('final_target_visibility')
    if not isinstance(proof,dict):return False
    area=proof.get('viewport');rows=proof.get('rows')
    if not valid_area(area) or area['width']<40 or area['height']<40:return False
    if not isinstance(rows,list) or len(rows)!=2:return False
    previous=None
    for row,target in zip(rows,record['targets']):
        if (not isinstance(row,dict) or row.get('runtime_id')!=target['runtime_id']
                or row.get('offscreen') is not False or row.get('within_viewport') is not True):return False
        b=row.get('bounds')
        if not valid_area(b):return False
        if not (b['top']>=area['top']-1 and b['top']+b['height']<=area['top']+area['height']+1
                and area['left']<b['left']+b['width']/2<area['left']+area['width']):return False
        if previous and b['top']<previous['top']+previous['height']-1:return False
        previous=b
    return True


def saved_contact_snapshot(store,record,plan=None):
    """UI-thread snapshot; background workers never use SQLite."""
    if not plan or plan.get('source')!='confirmed_batch':return []
    from pinned_members import same_binding
    if not same_binding(plan['record'],record):raise ValueError('联系人名单窗口和群绑定不对应')
    contacts=[]
    for member in plan['members']:
        row=store.db.execute('SELECT * FROM items WHERE id=?',(member['item_id'],)).fetchone()
        if (not row or row['account']!=record['account'] or row['batch_id'] not in plan.get('batch_ids',[plan['batch_id']])
                or str(row['contact_number'])!=member['number'] or not row['added_at']
                or row['status'] not in ('added','pending_invite','completed')):
            raise ValueError('联系人成功记录已变化，不自动整理资料页')
        if row['source']=='phone':contacts.append({'number':member['number'],'phone':row['value']})
    return contacts


def saved_addition_contacts(store,record,plan=None,*,window_history=False):
    """Include confirmed history from this exact account/window for page cleanup.

    These contacts authorize closing a matching profile only; they never extend
    an invitation plan or assign a number to an unconfirmed record.
    """
    contacts=saved_contact_snapshot(store,record,plan)
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contact_queue_jobs'").fetchone():return contacts
    from pinned_members import same_binding
    for job in store.db.execute('''SELECT j.batch_id,j.window_json,j.binding_json,b.account AS historic_account FROM contact_queue_jobs j
            JOIN batches b ON b.id=j.batch_id JOIN contact_queues q ON q.id=j.queue_id
            WHERE (b.account=? OR ?=1) AND q.target_mode IN ('pinned','contact') ''',(record['account'],int(window_history))):
        w=json.loads(job['window_json']);binding=json.loads(job['binding_json'])
        if (any(w.get(k)!=record['window'].get(k) for k in ('hwnd','pid'))
                or path_key(w)!=path_key(record['window'])
                or (not record.get('contact_only') and (not binding or not same_binding(binding,record)))):continue
        for row in store.db.execute('''SELECT value,contact_number FROM items WHERE batch_id=? AND account=?
                AND source='phone' AND status IN ('added','pending_invite','completed')
                AND added_at IS NOT NULL AND numbering_global=1 AND contact_number IS NOT NULL''',
                (job['batch_id'],job['historic_account'])):
            c={'number':str(row['contact_number']),'phone':row['value']}
            if c not in contacts:contacts.append(c)
    return contacts


def scroll_verified(report,record,actions):
    """Accept scrolling only with the saved rows and bounded input evidence."""
    rows=[t['runtime_id'] for t in record['targets']]
    if report.get('first_two_bound') is not True or report.get('first_two_runtime_ids')!=rows:return False
    scrolls=[a for a in actions if a['action']=='scroll_chat_list']
    method=report.get('scroll_method')
    events=report.get('wheel_events',[])
    if not scrolls:return method=='none' and events==[]
    if report.get('scroll_binding_verified') is not True:return False
    action=scrolls[0]
    if method=='ScrollPattern':return events==[]
    if method=='ScrollItemPattern':return action['runtime_id']==rows[0] and events==[]
    if method!='bound_mouse_wheel' or action['runtime_id']!=record['list_runtime_id']:return False
    if not isinstance(events,list) or not 1<=len(events)<=12:return False
    for index,event in enumerate(events,1):
        if (not isinstance(event,dict) or type(event.get('index')) is not int or event['index']!=index
                or event.get('list_runtime_id')!=record['list_runtime_id']
                or type(event.get('delta')) is not int or event['delta']!=1200
                or not all(event.get(k) is True for k in ('foreground_verified','point_in_list_verified','attempted','sent'))
                or not all(event.get(k) is False for k in ('button_events','keyboard_events'))):return False
        area=event.get('viewport')
        if not valid_area(area):return False
        if area['width']<40 or area['height']<40:return False
        if type(event.get('x')) is not int or type(event.get('y')) is not int:return False
        if not (area['left']+4<event['x']<area['left']+area['width']-4
                and area['top']+4<event['y']<area['top']+area['height']-4):return False
    return True


def search_verified(report,actions):
    actual='class Ui::InputField::Inner'
    if report.get('search_class')!=actual or not report.get('search_wrapper_runtime_id'):return False
    if report['search_wrapper_runtime_id']==report['search_runtime_id']:return False
    reads=report.get('search_reads')
    if not isinstance(reads,list) or len(reads)<3 or not all(isinstance(r,dict) for r in reads):return False
    if [reads[0].get('stage'),reads[1].get('stage'),reads[-1].get('stage')]!=['initial','before_scroll','final']:return False
    for index,read in enumerate(reads):
        if (read.get('runtime_id')!=report['search_runtime_id'] or read.get('class_name')!=actual
                or type(read.get('value_length')) is not int or read['value_length']<0
                or type(read.get('text_supported')) is not bool):return False
        if read['text_supported']:
            if type(read.get('text_length')) is not int or read['text_length']<0:return False
        elif read.get('text_length') is not None:return False
        if index and (read['value_length']!=0 or (read['text_supported'] and read['text_length']!=0)):return False
    nonempty=reads[0]['value_length']>0 or (reads[0]['text_supported'] and reads[0]['text_length']>0)
    clear=[a for a in actions if a['action']=='clear_chat_search']
    if bool(clear)!=bool(nonempty):return False
    return not clear or clear[0]['runtime_id']==report['search_runtime_id']


def cleanup_verified(report,record,contacts):
    w=record['window']
    if (not isinstance(report,dict) or report.get('ok') is not True
            or report.get('scope')!='group_page_cleanup' or report.get('state')!='ready'
            or report.get('window_handle')!=w['hwnd'] or report.get('process_id')!=w['pid']
            or path_key({'path':report.get('executable_path')})!=path_key(w)
            or report.get('target_names')!=[t['name'] for t in record['targets']]
            or not report.get('main_runtime_id') or not report.get('search_runtime_id')
            or (not record.get('contact_only') and report.get('list_runtime_id')!=record['list_runtime_id']) or report.get('errors')!=[]
            or not all(report.get(k) is True for k in (('process_path_verified','modal_absent','profile_absent','search_empty','contact_only_verified') if record.get('contact_only') else ('process_path_verified','modal_absent','profile_absent','search_empty','first_two_visible')))
            or not all(report.get(k) is False for k in ('members_selected','create_attempted','final_invite_clicked','contact_database_updated','invite_result_inferred'))):return False
    actions=report.get('actions')
    if not isinstance(actions,list):return False
    allowed=('cancel_member_selector','close_profile','clear_chat_search','scroll_chat_list')
    names=[a.get('action') for a in actions if isinstance(a,dict)]
    if (len(names)!=len(actions) or any(n not in allowed for n in names)
            or len(set(names))!=len(names) or names!=sorted(names,key=allowed.index)
            or not all(a.get('attempted') is True and a.get('invoked') is True and bool(a.get('runtime_id')) for a in actions)):return False
    if not search_verified(report,actions):return False
    if record.get('contact_only'):
        if report.get('contact_only') is not True or 'scroll_chat_list' in names:return False
    elif not target_visibility_verified(report,record) or not scroll_verified(report,record,actions):return False
    if 'close_profile' in names or 'cancel_member_selector' in names:
        if not report.get('profile_runtime_id') or report.get('profile_verified') is not True:return False
        if report.get('profile_kind')=='group':
            if (not record.get('contact_only') and report.get('profile_name') not in report['target_names']) or report.get('group_members_verified') is not True:return False
        elif report.get('profile_kind')=='contact':
            if 'cancel_member_selector' in names or report.get('phone_verified') is not True or report.get('saved_contact_verified') is not True:return False
            matches=[c for c in contacts if c['number']==report.get('profile_name')]
            if len(matches)!=1 or report.get('phone_sha256')!=hashlib.sha256(matches[0]['phone'].encode()).hexdigest():return False
        else:return False
    if 'cancel_member_selector' in names and (not report.get('dialog_runtime_id') or report.get('member_dialog_verified') is not True):return False
    return True


def prepare_group_page(record,contacts,path):
    """Pending action is journaled before native input; no automatic retry."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    result={'scope':'group_page_preparation','state':'pending','record':copy.deepcopy(record),
        'allowed_contact_numbers':[c['number'] for c in contacts],
        'final_invite_clicked':False,'contact_database_updated':False,'invite_result_inferred':False}
    def save():
        temp=path.with_suffix('.json.tmp');temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    save()
    try:
        report=run_window_script(record['window'],'prepare_group_page.ps1',{
            'executable_path':record['window']['path'],'list_runtime_id':record['list_runtime_id'],
            'target_names':[t['name'] for t in record['targets']],
            'targets':copy.deepcopy(record['targets']), 'contacts':contacts,'contact_only':record.get('contact_only',False)})
        result['report']=report
        if not cleanup_verified(report,record,contacts):raise ValueError('页面整理结果核对不完整，未进入群搜索选人')
        result['state']='ready';save();return result
    except Exception as error:
        if isinstance(error,WindowActionError):result['report']=error.report
        result.update(state='review',reason=str(error));save()
        raise WindowActionError(str(error),result) from error


def clean_group_pages(records,contacts_by_account,path,progress=None):
    from group_navigation import navigation_entries
    records=list(records)
    if not all(r.get('contact_only') for r in records):records=navigation_entries(records)
    else:
        from contact_queue import addition_windows
        addition_windows([r['window'] for r in records])
        if len({r['account'] for r in records})!=len(records):raise ValueError('账号备注重复')
    path=Path(path)
    result={'scope':'group_page_cleanup_queue','ok':False,'state':'pending',
        'jobs':[{'account':r['account'],'state':'not_started'} for r in records],
        'final_invite_clicked':False,'contact_database_updated':False,'invite_result_inferred':False}
    def save():
        temp=path.with_suffix('.json.tmp');temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    save();current=None
    try:
        for record,job in zip(records,result['jobs']):
            current=job;job['state']='cleaning';save()
            if progress:progress(record['account']+'：整理页面，不选人、不邀请')
            job['cleanup']=prepare_group_page(record,contacts_by_account.get(record['account'],[]),path.with_name(path.stem+'_'+str(record['window']['hwnd'])+'.json'))
            job['state']='ready';save()
        result.update(ok=True,state='ready')
    except Exception as error:
        if current:
            current['state']='review'
            if isinstance(error,WindowActionError):current['cleanup']=error.report
        result.update(state='review',reason=str(error)+'；后续窗口未整理，不自动重试')
    save();return result


def prepare_pinned_scan(window):
    report=run_window_script(window,'prepare_group_page.ps1',{
        'executable_path':window['path'],'contact_only':True,'scan_prepare':True,
        'contacts':[],'target_names':[],'targets':[]})
    if not (report.get('ok') is True and report.get('scan_prepare') is True
            and report.get('window_handle')==window['hwnd'] and report.get('process_id')==window['pid']
            and all(report.get(k) is True for k in ('modal_absent','profile_absent','search_empty'))
            and report.get('final_invite_clicked') is False and report.get('create_attempted') is False):
        raise ValueError('扫描前页面整理未完整确认；未读取或覆盖置顶群绑定')
    return report
