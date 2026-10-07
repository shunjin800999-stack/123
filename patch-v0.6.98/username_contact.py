"""Bound username contact proof, based on observed Telegram Desktop controls."""
import re
from pinned_scan import path_key


def normalized_username(value):
    if not isinstance(value,str) or not re.fullmatch(r'@[A-Za-z0-9_]{1,32}',value):
        raise ValueError('用户名需要@开头，后接字母、数字或下划线')
    return value.lower()


def username_profile_verified(report,window,username,number=None):
    if not isinstance(report,dict):return False
    if not (report.get('ok') is True and report.get('read_only') is True
        and report.get('scope')=='profile' and report.get('scope_class')=='class Info::Profile::Widget'
        and report.get('window_handle')==window['hwnd'] and report.get('process_id')==window['pid']
        and report.get('scope_runtime_id') and report.get('truncated') is False and report.get('errors')==[]):return False
    rows=[c for c in report.get('controls',[]) if c.get('visible') is True]
    identities=[c for c in rows if c.get('type')=='Text' and c.get('class_name')=='class Ui::FlatLabel'
        and str(c.get('name','')).lower()==normalized_username(username)]
    titles=[c for c in rows if c.get('type')=='Text' and c.get('class_name')=='class Ui::MarqueeLabel']
    def buttons(names):return [c for c in rows if c.get('type')=='Button' and c.get('enabled') is True
        and c.get('class_name')=='class Ui::SettingsButton' and c.get('name') in names]
    add=buttons(('ADICIONAR CONTATO','ADD CONTACT','Add contact','Add to contacts','ADD TO CONTACTS'))
    edit=buttons(('Editar contato','Edit contact'));delete=buttons(('Apagar contato','Delete contact'))
    if len(identities)!=1 or len(titles)!=1:return False
    if number is None:return len(add)==1 and not edit and not delete and bool(titles[0].get('name'))
    return titles[0].get('name')==str(number) and len(edit)==1 and len(delete)==1 and not add


def _bound(report,job,mode):
    w=job['window']
    return (isinstance(report,dict) and report.get('ok') is True and report.get('scope')=='username_contact'
        and report.get('mode')==mode and report.get('window_handle')==w['hwnd']
        and report.get('process_id')==w['pid'] and report.get('username')==normalized_username(job['item']['value'])
        and path_key({'path':report.get('executable_path')})==path_key(w)
        and bool(report.get('main_runtime_id')) and bool(report.get('contact_runtime_id'))
        and report.get('process_path_verified') is True and report.get('username_verified') is True
        and report.get('field_schema_verified') is True and report.get('errors')==[]
        and report.get('final_invite_clicked') is False and report.get('contact_database_updated') is False)


def opening_verified(report,job):
    return (_bound(report,job,'open') and report.get('stage')=='verified'
        and report.get('state')=='username_form_open' and report.get('default_instance_route_verified') is True
        and report.get('add_invoked') is True and bool(report.get('profile_runtime_id'))
        and report.get('fields_written') is False and report.get('create_attempted') is False and report.get('create_invoked') is False)


def fill_verified(report,job):
    opened=job.get('opening_report') or {}
    return (_bound(report,job,'fill') and opening_verified(opened,job)
        and report.get('main_runtime_id')==opened.get('main_runtime_id')
        and report.get('contact_runtime_id')==opened.get('contact_runtime_id')
        and report.get('profile_runtime_id')==opened.get('profile_runtime_id')
        and report.get('number')==str(job['preview_number']) and report.get('stage')=='verified'
        and report.get('fields_verified') is True and report.get('fields_written') is True
        and report.get('create_attempted') is False and report.get('create_invoked') is False and report.get('state')=='filled')


def submission_payload(job):
    fill=job.get('fill_report') or {}
    if not fill_verified(fill,job):raise ValueError('用户名表单未完整核对，不能提交')
    return {'mode':'submit','username':normalized_username(job['item']['value']),
        'number':str(job['preview_number']),'executable_path':job['window']['path'],
        'main_runtime_id':fill['main_runtime_id'],'contact_runtime_id':fill['contact_runtime_id'],
        'profile_runtime_id':fill['profile_runtime_id']}


def submission_verified(report,job):
    fill=job.get('fill_report') or {}
    if not (_bound(report,job,'submit') and fill_verified(fill,job)
        and report.get('main_runtime_id')==fill.get('main_runtime_id')
        and report.get('contact_runtime_id')==fill.get('contact_runtime_id')
        and report.get('number')==str(job['preview_number']) and report.get('fields_verified') is True
        and report.get('fields_written') is False and report.get('create_attempted') is True
        and report.get('create_invoked') is True and bool(report.get('create_runtime_id'))
        and report.get('stage')=='submitted'):return False
    if report.get('state')=='profile_opened':
        return bool(report.get('profile_runtime_id')) and report.get('profile_verified') is True
    if report.get('state')!='awaiting_profile' or report.get('profile_verified') is not False:return False
    if report.get('submit_method')=='verified_modal_done_mouse_sendinput_wait_close_reopen':
        if not (report.get('submit_form_preserved_until_exit') is True
                and report.get('submitted_form_close_attempted') is not True
                and report.get('submitted_form_close_invoked') is not True):return False
        original=dict(report,submit_method='verified_modal_done_mouse_sendinput_close_reopen')
        return submission_verified(original,job)
    if report.get('submit_method')=='verified_modal_done_mouse_sendinput_close_reopen':
        if not (report.get('refresh_close_before_reopen') is True
                and report.get('refresh_previous_profile_absent') is True):return False
        if report.get('refresh_close_attempted') is True and not (
                report.get('refresh_close_mouse_down_count')==1 and report.get('refresh_close_mouse_up_count')==1
                and bool(report.get('refresh_close_runtime_id'))):return False
        original=dict(report,submit_method='verified_modal_done_mouse_sendinput_direct_reopen')
        return submission_verified(original,job)
    if report.get('submit_method')=='verified_modal_done_mouse_sendinput_direct_reopen':
        if not (report.get('contact_form_absent') is True and report.get('profile_refresh_attempted') is True
                and report.get('profile_reopened') is True
                and report.get('profile_refresh_username')==normalized_username(job['item']['value'])
                and bool(report.get('profile_runtime_id'))):return False
        original=dict(report,submit_method='verified_modal_done_mouse_sendinput')
        return submission_verified(original,job)
    if report.get('submit_method')=='verified_modal_done_mouse_sendinput_refresh':
        if not (report.get('contact_form_absent') is True and report.get('refresh_previous_profile_absent') is True
                and report.get('profile_refresh_attempted') is True and report.get('profile_refreshed') is True
                and report.get('profile_refresh_username')==normalized_username(job['item']['value'])
                and bool(report.get('profile_runtime_id'))):return False
        original=dict(report,submit_method='verified_modal_done_mouse_sendinput')
        return submission_verified(original,job)
    if report.get('submit_method')=='verified_modal_done_mouse_sendinput':
        return (report.get('physical_pixel_context') is True and report.get('click_target_verified') is True
            and report.get('click_target_reads')==2
            and report.get('click_modal_containment_verified') is True
            and report.get('click_native_window_verified') is True
            and report.get('click_modal_runtime_id')==fill.get('dialog_runtime_id')
            and bool(report.get('click_modal_runtime_id'))
            and report.get('click_target_runtime_id')==report.get('create_runtime_id')
            and report.get('click_target_class')=='class Ui::RoundButton'
            and report.get('click_target_name') in ('Done','Pronto')
            and report.get('submit_mouse_down_count')==1 and report.get('submit_mouse_up_count')==1)
    if report.get('submit_method')=='verified_physical_done_mouse_sendinput':
        return (report.get('physical_pixel_context') is True and report.get('click_target_verified') is True
            and report.get('click_target_reads')==2
            and report.get('submit_mouse_down_count')==1 and report.get('submit_mouse_up_count')==1
            and bool(report.get('click_hit_runtime_id'))
            and report.get('click_target_runtime_id')==report.get('create_runtime_id')
            and report.get('click_target_class')=='class Ui::RoundButton'
            and report.get('click_target_name') in ('Done','Pronto'))
    if report.get('submit_method')=='verified_native_window_mouse_click':
        return report.get('click_target_verified') is True
    if report.get('submit_method')=='verified_focused_done_space':
        return (report.get('focus_target_verified') is True and report.get('focus_reads')==2
            and report.get('submit_focus_runtime_id')==report.get('actual_focus_runtime_id')==report.get('create_runtime_id')
            and bool(report.get('create_runtime_id'))
            and report.get('actual_focus_name') in ('Done','Pronto')
            and report.get('actual_focus_class')=='class Ui::RoundButton'
            and report.get('submit_key_down_count')==1 and report.get('submit_key_up_count')==1)
    if report.get('submit_method')=='verified_focused_last_name_enter_sendinput':
        return (report.get('focus_target_verified') is True and report.get('focus_reads')==2
            and report.get('submit_focus_runtime_id')==report.get('actual_focus_runtime_id')
            and bool(report.get('submit_focus_runtime_id'))
            and report.get('actual_focus_name') in ('Last name','Sobrenome')
            and report.get('actual_focus_class')=='class Ui::InputField::Inner'
            and report.get('submit_key_down_count')==1 and report.get('submit_key_up_count')==1
            and report.get('submit_scan_code')==28)
    return (report.get('submit_method')=='verified_focused_last_name_enter'
        and report.get('focus_target_verified') is True and report.get('focus_reads')==2
        and report.get('submit_focus_runtime_id')==report.get('actual_focus_runtime_id')
        and bool(report.get('submit_focus_runtime_id'))
        and report.get('actual_focus_name') in ('Last name','Sobrenome')
        and report.get('actual_focus_class')=='class Ui::InputField::Inner')


def username_form_schema(report,window):
    """Read-only diagnostic schema; identity still comes from the bound opening."""
    if not (report.get('ok') is True and report.get('read_only') is True and report.get('scope')=='dialog'
        and report.get('scope_class')=='class Ui::BoxLayerWidget' and report.get('scope_runtime_id')
        and report.get('window_handle')==window['hwnd'] and report.get('process_id')==window['pid']
        and report.get('truncated') is False and report.get('errors')==[]):return False
    rows=[c for c in report.get('controls',[]) if c.get('visible') is True]
    if len([c for c in rows if c.get('type')=='Window' and c.get('class_name')=='class Ui::GenericBox'])!=1:return False
    if any(c.get('class_name') in ('class AddContactBox','class Ui::PhoneInput') for c in rows):return False
    inner=[c for c in rows if c.get('type')=='Edit' and c.get('class_name')=='class Ui::InputField::Inner']
    if len(inner)!=3 or any(c.get('enabled') is not True or c.get('value_pattern') is not True for c in inner):return False
    labels={c.get('name') for c in inner}
    if labels not in ({'Nome','Sobrenome','Nota'},{'First name','Last name','Note'}):return False
    for names in (('Novo Contato','New Contact'),('Pronto','Done'),('Cancelar','Cancel')):
        kind='Text' if names[0]=='Novo Contato' else 'Button'
        if len([c for c in rows if c.get('type')==kind and c.get('name') in names and c.get('enabled') is True])!=1:return False
    return True


def missing_username_text(username,language='pt'):
    value=normalized_username(username)
    if language=='pt':return 'Não existe conta no Telegram com o nome de usuário '+value+'.'
    if language=='en':return 'Username '+value+' not found.'
    raise ValueError('用户名不存在提示语言未验证')


def missing_username_texts(username):
    return (missing_username_text(username,'pt'),missing_username_text(username,'en'))


def missing_username_evidence(reports,window,username):
    """Only the observed explicit non-existent username message, never vague errors."""
    if len(reports)!=2:return None
    ids=[]
    for r in reports:
        if not (isinstance(r,dict) and r.get('ok') is True and r.get('read_only') is True
            and r.get('scope')=='dialog' and r.get('scope_class')=='class Ui::BoxLayerWidget'
            and r.get('scope_runtime_id') and r.get('window_handle')==window['hwnd']
            and r.get('process_id')==window['pid'] and r.get('truncated') is False and r.get('errors')==[]):return None
        rows=[c for c in r.get('controls',[]) if c.get('visible') is True]
        texts=[c for c in rows if c.get('type')=='Text']
        buttons=[c for c in rows if c.get('type')=='Button']
        boxes=[c for c in rows if c.get('type')=='Window']
        if (len(texts)!=1 or texts[0].get('class_name')!='class Ui::FlatLabel'
                or texts[0].get('name') not in missing_username_texts(username)
                or len(buttons)!=1 or buttons[0].get('name')!='OK'
                or buttons[0].get('class_name')!='class Ui::RoundButton' or buttons[0].get('enabled') is not True
                or len(boxes)!=1 or boxes[0].get('class_name')!='class Ui::GenericBox'
                or any(c.get('type')=='Edit' for c in rows)):return None
        ids.append((r['scope_runtime_id'],texts[0]['name']))
    if ids[0]!=ids[1]:return None
    return {'dialog_runtime_id':ids[0][0],'text':ids[0][1]}


def missing_result_verified(report,job,expected_dialog=None):
    w=job['window']
    return (isinstance(report,dict) and report.get('ok') is True and report.get('scope')=='username_contact'
        and report.get('mode') in ('open','dismiss_missing') and report.get('state')=='username_not_found'
        and report.get('stage')=='verified' and report.get('window_handle')==w['hwnd']
        and report.get('process_id')==w['pid'] and report.get('username')==normalized_username(job['item']['value'])
        and path_key({'path':report.get('executable_path')})==path_key(w)
        and bool(report.get('main_runtime_id')) and bool(report.get('missing_dialog_runtime_id'))
        and (expected_dialog is None or report.get('missing_dialog_runtime_id')==expected_dialog)
        and report.get('not_found_text') in missing_username_texts(job['item']['value'])
        and report.get('not_found_verified') is True and report.get('not_found_reads')==2
        and report.get('process_path_verified') is True
        and (report.get('mode')!='open' or report.get('default_instance_route_verified') is True)
        and report.get('dismiss_attempted') is True and report.get('dismiss_invoked') is True
        and report.get('result_absent') is True and bool(report.get('dismiss_runtime_id'))
        and report.get('errors')==[]
        and all(report.get(k) is False for k in ('fields_written','add_invoked','create_attempted','create_invoked',
            'final_invite_clicked','contact_database_updated')))


def saved_profile_close_verified(report, job):
    if not isinstance(report,dict):return False
    w=job['window']
    return (report.get('ok') is True and report.get('scope')=='username_contact'
        and report.get('mode')=='close_saved' and report.get('state')=='saved_profile_closed'
        and report.get('window_handle')==w['hwnd'] and report.get('process_id')==w['pid']
        and str(report.get('executable_path','')).casefold()==w['path'].casefold()
        and report.get('username')==normalized_username(job['item']['value'])
        and report.get('number')==str(job['preview_number'])
        and report.get('main_runtime_id')==job['opening_report']['main_runtime_id']
        and report.get('profile_runtime_id')==job['close_profile_runtime_id']
        and all(report.get(k) is True for k in ('process_path_verified','profile_verified','close_attempted','close_invoked','profile_absent'))
        and all(report.get(k) is False for k in ('fields_written','add_invoked','create_attempted','create_invoked'))
        and not report.get('errors'))
