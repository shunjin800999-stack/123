"""Only verified dismissal of an observed unregistered result can release its task."""
def dismissal_verified(r,job,dialog_id):
    w=job['window'];fill=job.get('fill_report') or {}
    return (isinstance(r,dict) and r.get('ok') is True and r.get('scope')=='unregistered_contact'
        and r.get('window_handle')==w['hwnd'] and r.get('process_id')==w['pid']
        and r.get('main_runtime_id')==fill.get('main_runtime_id')
        and r.get('dialog_runtime_id')==dialog_id and r.get('number')==str(job['preview_number'])
        and all(r.get(k) is True for k in ('process_path_verified','signature_verified','dismiss_attempted','dismiss_invoked','result_absent'))
        and all(r.get(k) is False for k in ('create_attempted','final_invite_clicked','contact_database_updated')))
