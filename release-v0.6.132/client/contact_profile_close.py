"""Close a verified saved contact profile only; no generic popup dismissal."""
from controls_probe import verify_contact_profile
from pinned_scan import path_key


def previous_profile_verified(reports,window,previous):
    if len(reports) not in (1,2):return False
    first=reports[0]
    return (bool(first.get('scope_runtime_id')) and
        all(r.get('ok') is True and r.get('read_only') is True and r.get('scope')=='profile'
            and r.get('scope_class')=='class Info::Profile::Widget'
            and r.get('window_handle')==window['hwnd'] and r.get('process_id')==window['pid']
            and r.get('scope_runtime_id')==first['scope_runtime_id']
            and r.get('truncated') is False and r.get('errors')==[]
            and verify_contact_profile(r,previous['value'],str(previous['contact_number']))['verified']
            for r in reports))


def profile_close_verified(report,window,action):
    return (isinstance(report,dict) and report.get('ok') is True
        and report.get('scope')=='contact_profile_close' and report.get('state')=='closed' and report.get('stage')=='verified'
        and report.get('window_handle')==window['hwnd'] and report.get('process_id')==window['pid']
        and path_key({'path':report.get('executable_path')})==path_key(window)
        and report.get('profile_runtime_id')==action['profile_runtime_id']
        and report.get('number')==str(action['previous_number'])
        and bool(report.get('main_runtime_id')) and bool(report.get('close_runtime_id'))
        and report.get('errors')==[]
        and all(report.get(k) is True for k in ('process_path_verified','profile_fields_verified','close_attempted',
            'close_invoked','profile_absent','modal_absent'))
        and all(report.get(k) is False for k in ('fields_written','create_attempted','contact_database_updated','final_invite_clicked')))


def profile_close_payload(job):
    action=job['profile_closing']
    return {'phone':action['previous_phone'],'number':str(action['previous_number']),
        'profile_runtime_id':action['profile_runtime_id'],'executable_path':job['window']['path']}
