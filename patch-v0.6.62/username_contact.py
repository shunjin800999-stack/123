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
    add=buttons(('ADICIONAR CONTATO','ADD CONTACT','Add contact'))
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
    return (_bound(report,job,'submit') and fill_verified(fill,job)
        and report.get('main_runtime_id')==fill.get('main_runtime_id')
        and report.get('contact_runtime_id')==fill.get('contact_runtime_id')
        and report.get('number')==str(job['preview_number']) and report.get('fields_verified') is True
        and report.get('fields_written') is False and report.get('create_attempted') is True
        and report.get('create_invoked') is True and bool(report.get('create_runtime_id'))
        and report.get('state')=='profile_opened' and report.get('stage')=='submitted'
        and bool(report.get('profile_runtime_id')) and report.get('profile_verified') is True)


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
