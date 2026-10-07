"""Open each bound empty form once. No reservation, numbering or Create."""
import copy
import json
from pathlib import Path

from controls_probe import run_window_script, WindowActionError
from group_navigation import navigation_entries
from pinned_scan import path_key


def form_open_verified(report,window):
    return (isinstance(report,dict) and report.get('ok') is True
        and report.get('scope')=='contact_form_open' and report.get('mode')=='open_only'
        and report.get('state') in ('opened_empty','existing_empty') and report.get('stage')=='verified'
        and report.get('window_handle')==window['hwnd'] and report.get('process_id')==window['pid']
        and path_key({'path':report.get('executable_path')})==path_key(window)
        and report.get('main_runtime_id') and report.get('dialog_runtime_id') and report.get('contact_runtime_id')
        and all(report.get(k) is True for k in ('process_path_verified','default_instance_route_verified',
            'field_schema_verified','empty_form_verified'))
        and report.get('errors')==[]
        and all(report.get(k) is False for k in ('fields_written','create_clicked','contact_created',
            'contact_database_updated','final_invite_clicked'))
        and ((report['state']=='opened_empty' and report.get('launch_requested') is True and report.get('launch_started') is True)
            or (report['state']=='existing_empty' and report.get('launch_requested') is False and report.get('launch_started') is False)))


def open_contact_forms(observations,report_path,progress=None,*,runner=None):
    entries=navigation_entries(observations)
    if len({path_key(r['window']) for r in entries})!=len(entries):
        raise ValueError('同一程序路径有多个窗口，无法确定联系人表单应打开在哪个账号')
    path=Path(report_path).resolve()
    if path.suffix.lower()!='.json':raise ValueError('请使用 JSON 报告路径')
    jobs=[{'index':i,'account':r['account'],'window':copy.deepcopy(r['window']),
        'state':'not_started','reason':''} for i,r in enumerate(entries,1)]
    result={'ok':False,'scope':'contact_form_open_queue','state':'preparing','jobs':jobs,
        'report_path':str(path),'contact_database_updated':False,'fields_written':False,
        'create_clicked':False,'contact_created':False,'final_invite_clicked':False,'reason':''}
    def save():
        temp=path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    save()
    runner=runner or run_window_script
    current=None
    try:
        for job in jobs:
            current=job;job['state']='opening';result['state']='running';save()
            if progress:progress(f'{job["index"]}/{len(jobs)} · {job["account"]}：直达空白 New Contact')
            report=runner(job['window'],'open_contact.ps1',{'executable_path':job['window']['path']})
            job['opening']=report
            if not form_open_verified(report,job['window']):raise ValueError('未完整核对指定窗口中的空白联系人表单，停止，不自动重试')
            job['state']='empty_form_open';job['reason']='空白表单已核对，未填写、未提交';save()
        result.update(ok=True,state='empty_forms_open',reason='所有所选账号的空白 New Contact 已打开并核对；未填写、未点击 Create。')
    except Exception as error:
        if current:
            current.update(state='review',reason=str(error))
            if isinstance(error,WindowActionError):current['opening']=error.report
        result.update(state='review',reason=str(error)+'；已打开表单保留，后续账号未执行，不自动重试。')
    save()
    return result
