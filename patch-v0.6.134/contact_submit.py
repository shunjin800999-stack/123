"""Bound Create/result navigation proof. A submitted form is not a saved contact."""
def submission_verified(report,job):
    if job['item']['source']=='username':
        from username_contact import submission_verified as verify_username
        return verify_username(report,job)
    fill=job.get('fill_report') or {};w=job['window']
    if not (isinstance(report,dict) and report.get('ok') is True and report.get('scope')=='contact_submit'
            and report.get('stage')=='submitted' and report.get('state') in ('profile_opened','result_dialog')
            and report.get('window_handle')==w['hwnd'] and report.get('process_id')==w['pid']
            and report.get('main_runtime_id')==fill.get('main_runtime_id') and bool(report.get('main_runtime_id'))
            and report.get('contact_runtime_id')==fill.get('contact_runtime_id') and bool(report.get('contact_runtime_id'))
            and report.get('number')==str(job['preview_number']) and report.get('fields_verified') is True
            and report.get('process_path_verified') is True and report.get('create_attempted') is True
            and report.get('create_invoked') is True and bool(report.get('create_runtime_id')) and report.get('errors')==[]
            and all(report.get(k) is False for k in ('fields_written','contact_created_verified','contact_database_updated','final_invite_clicked'))):
        return False
    if report['state']=='profile_opened':
        if not (all(report.get(k) is True for k in ('title_verified','profile_opened')) and bool(report.get('profile_runtime_id'))):
            return False
        if report.get('profile_already_open') is True:
            return (report.get('result_page_verified') is True and
                all(report.get(k) is False for k in ('profile_open_attempted','profile_open_invoked')))
        return (report.get('profile_already_open',False) is False and
            all(report.get(k) is True for k in ('profile_open_attempted','profile_open_invoked')))
    return (bool(report.get('result_runtime_id')) and
        all(report.get(k) is False for k in ('profile_open_attempted','profile_open_invoked','profile_opened')))


def submission_payload(job):
    if job['item']['source']=='username':
        from username_contact import submission_payload as username_payload
        return username_payload(job)
    fill=job.get('fill_report') or {};w=job['window']
    if (fill.get('window_handle')!=w['hwnd'] or fill.get('process_id')!=w['pid']
            or not fill.get('main_runtime_id') or not fill.get('contact_runtime_id')
            or fill.get('number')!=str(job['preview_number']) or fill.get('ok') is not True
            or fill.get('mode')!='fill_only' or fill.get('phone_matches') is not True
            or fill.get('empty_guard') is not True or fill.get('contact_created') is not False
            or fill.get('final_invite_clicked') is not False):
        raise ValueError('试填报告缺少原窗口和表单身份，不自动点击 Create')
    return {'phone':job['item']['value'],'number':str(job['preview_number']),
        'executable_path':w['path'],'main_runtime_id':fill['main_runtime_id'],
        'contact_runtime_id':fill['contact_runtime_id']}
