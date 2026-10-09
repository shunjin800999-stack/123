"""Collect a bounded, read-only UIA control report from one selected window."""
import json
import os
import re
from pathlib import Path
import subprocess
import hashlib
import time

from windows_scan import scan_windows
from native_member_session import SESSION as MEMBER_SESSION, USERNAME_SESSION
from visual_members import numeric_label


class WindowActionError(RuntimeError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report=report


def run_window_script(window, script_name, payload=None):
    operation_started=time.monotonic()
    if os.name != 'nt':
        raise RuntimeError('界面检查和填写只能在你的 Windows 电脑运行')
    fresh = next((row for row in scan_windows() if row['hwnd']==window['hwnd']
                  and row['pid']==window['pid'] and row['path']==window['path']),None)
    if not fresh:
        raise ValueError('窗口身份已改变或已关闭，请重新扫描并选择')
    if script_name not in ('inspect_controls.ps1','fill_contact.ps1','inspect_member_visual.ps1','select_member.ps1','inspect_groups.ps1','navigate_group.ps1','open_contact.ps1','submit_contact.ps1','close_contact_profile.ps1','prepare_group_page.ps1','scroll_member_header.ps1','unregistered_contact.ps1','username_contact.ps1'):
        raise ValueError('未知界面操作')
    script_path=Path(__file__).with_name(script_name)
    if not script_path.is_file():raise RuntimeError('未找到窗口操作脚本：'+script_name)
    system_root=os.environ.get('SystemRoot',r'C:\Windows')
    powershell=Path(system_root)/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    if not powershell.is_file():
        raise RuntimeError('未找到 Windows PowerShell，请保留错误信息')
    env=os.environ.copy()
    env['TG_INSPECT_HWND']=str(int(window['hwnd']))
    env['TG_INSPECT_PID']=str(int(window['pid']))
    env['TG_ASSISTANT_DIR']=str(Path(__file__).resolve().parent)
    env['TG_ACTION_SCRIPT_NAME']=script_name
    if payload is not None:
        env['TG_ACTION_PAYLOAD']=json.dumps(payload,ensure_ascii=False)
    else:
        env.pop('TG_ACTION_PAYLOAD',None)
    try:
        timeout=30 if script_name in ('inspect_member_visual.ps1','select_member.ps1','navigate_group.ps1','open_contact.ps1','submit_contact.ps1','close_contact_profile.ps1','prepare_group_page.ps1') else 15
        if script_name=='username_contact.ps1':timeout=35
        if script_name=='scroll_member_header.ps1':timeout=120
        if script_name=='inspect_member_visual.ps1' and payload and payload.get('header_diagnostic'):
            timeout=45
        if script_name=='username_contact.ps1':
            completed=USERNAME_SESSION.run(powershell,env,script_name,payload,timeout)
        elif script_name in ('select_member.ps1','inspect_member_visual.ps1','inspect_groups.ps1','prepare_group_page.ps1','navigate_group.ps1','inspect_controls.ps1','close_contact_profile.ps1'):
            completed=MEMBER_SESSION.run(powershell,env,script_name,payload,timeout)
        else:
            bootstrap="& ([ScriptBlock]::Create([IO.File]::ReadAllText((Join-Path $env:TG_ASSISTANT_DIR $env:TG_ACTION_SCRIPT_NAME),[Text.Encoding]::UTF8)))"
            completed=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-Command',bootstrap],
                                     env=env,capture_output=True,timeout=timeout,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        if script_name=='scroll_member_header.ps1':
            raise RuntimeError('读取已选区域超时；滚动位置可能已改变，未选人或点击 Add，请保留页面和报告。') from None
        if script_name=='prepare_group_page.ps1':
            raise RuntimeError('整理群页面超时；部分面板或搜索可能已整理，请核查实际页面。未开始选人或邀请，不自动重试。') from None
        if script_name=='close_contact_profile.ps1':
            raise RuntimeError('关闭资料面板超时，请核查实际页面；未开始下一位联系人，不自动重试。') from None
        if script_name=='submit_contact.ps1':
            raise RuntimeError('联系人提交操作超时，Create 可能已执行；保留占用并核查实际资料页，不自动重复提交。') from None
        if script_name=='open_contact.ps1':
            raise RuntimeError('打开 New Contact 超时，请核查实际窗口；不自动重试。未填写或点击 Create。') from None
        if script_name=='select_member.ps1':
            raise RuntimeError('单人选择操作超时，结果未确认。请查看 Telegram，不要直接重试；最终 Add 未由脚本点击。') from None
        raise RuntimeError(f'操作超过 {timeout} 秒，已终止。若正在试填，部分字段可能已改变；请手动检查，未提交联系人。') from None
    text=completed.stdout.decode('utf-8-sig',errors='replace').strip()
    try:
        report=json.loads(text)
    except json.JSONDecodeError:
        error=completed.stderr.decode('utf-8-sig',errors='replace').strip()
        if script_name=='prepare_group_page.ps1':
            raise RuntimeError('整理群页面未返回完整结果：'+(error[:1200] or '请检查实际窗口')+'；未开始选人或邀请，不自动重试。') from None
        if script_name=='close_contact_profile.ps1':
            raise RuntimeError('关闭资料面板未返回完整结果：'+(error[:1200] or '请检查实际窗口')+'；未开始下一位，不自动重试。') from None
        if script_name=='submit_contact.ps1':
            raise RuntimeError('联系人提交未返回完整结果：'+(error[:1200] or '请检查实际窗口')+'；Create 可能已执行，不自动重复提交。') from None
        if script_name=='open_contact.ps1':
            raise RuntimeError('打开 New Contact 未返回完整核验：'+(error[:1200] or '请检查实际窗口')+'；不自动重试，未填写或提交。') from None
        raise RuntimeError('界面操作未返回完整结果：'+(error[:1200] or '请查看所选窗口')+'。若正在试填，请核查字段；程序未提交联系人。') from None
    if hasattr(completed,'member_timing'):report['operation_timing']=completed.member_timing
    elif script_name=='username_contact.ps1':
        report['operation_timing']={'transport':'standalone_powershell','seconds':round(time.monotonic()-operation_started,3)}
    if completed.returncode or not report.get('ok'):
        changed=report.get('changed_fields',[])
        suffix=('；已改变的字段：'+', '.join(changed)+'。请手动检查，未提交联系人。') if changed else ''
        raise WindowActionError('操作未完成：'+report.get('error','请查看窗口')+suffix,report)
    report['window_title']=fresh['title']
    report['executable']=Path(fresh['path']).name
    return report


def inspect_controls(window, *, queue_probe=False):
    return run_window_script(window,'inspect_controls.ps1',{'queue_probe':True} if queue_probe else None)


def inspect_groups(window, report_path):
    path=Path(report_path)
    if path.suffix.lower()!='.json':raise ValueError('群列表检测报告请保存为 JSON')
    try:
        report=run_window_script(window,'inspect_groups.ps1')
    except WindowActionError as error:
        report=error.report
        report['reason']=str(error)
    except (ValueError,RuntimeError,OSError) as error:
        report={'ok':False,'read_only':True,'scope':'group_catalog_probe','error':str(error),
            'group_count':None,'all_groups_scanned':False,'account_identity_verified':False,
            'final_invite_clicked':False,'expected_window':dict(window)}
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def inspect_member_visual(window, image_path, number, *, header_diagnostic=False):
    from visual_members import analyze_member_visual
    number=numeric_label(number)
    path=Path(image_path).resolve()
    if path.suffix.lower()!='.png':raise ValueError('截图请保存为 PNG')
    payload={'image_path':str(path)}
    if header_diagnostic:
        payload['header_diagnostic']=True
        path.with_name(path.stem+'_header.png').unlink(missing_ok=True)
    report=run_window_script(window,'inspect_member_visual.ps1',payload)
    report['analysis']=analyze_member_visual(report,number)
    report_path=path.with_suffix('.json')
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def inspect_member_search(window, image_path, number, *, expected_dialog_id=None):
    """Prepare and read back search before OCR; never select or invite anyone."""
    from visual_members import analyze_member_visual
    name=numeric_label(number)
    path=Path(image_path).resolve()
    if path.suffix.lower()!='.png':raise ValueError('截图请保存为 PNG')
    payload={'mode':'prepare','image_path':str(path),'number':name}
    if expected_dialog_id is not None:payload['expected_dialog_id']=expected_dialog_id
    report=run_window_script(window,'select_member.ps1',payload)
    search=report.get('search_preparation') or {}
    if search.get('number')!=name or search.get('value_matches') is not True:
        raise ValueError('未确认初始搜索框已填写完整数字备注，停止选择')
    report['analysis']=analyze_member_visual(report,name)
    path.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


class MemberSelectionPaused(RuntimeError):
    pass


def check_member_pause(should_stop):
    if should_stop is not None and should_stop():
        raise MemberSelectionPaused('已暂停选人，当前选择及报告保留；未提交邀请')


def select_member_test(window, image_path, number, *, expected_selected=(), expected_dialog=None, header_reader=None, fast_visible=False, should_stop=None):
    """Journal before one click; verify afterward, never retry a click or mark invited."""
    from visual_members import plan_member_selection, verify_member_selection, member_labels, member_header_guard, analyze_member_visual, verify_visible_member_selection
    name=numeric_label(number)
    expected_selected=member_labels(expected_selected,allow_empty=True)
    path=Path(image_path).resolve()
    if path.suffix.lower()!='.png':raise ValueError('截图请保存为 PNG')
    before_path=path.with_name(path.stem+'_before.png')
    journal_path=path.with_suffix('.json')
    result={'ok':False,'scope':'single_member_selection','read_only':False,
        'number':name,'state':'preparing','click_requested':False,'click_sent':False,
        'expected_selected':expected_selected,
        'search_requested':True,'search_applied':False,
        'selection_verified':False,'final_invite_clicked':False,'image_path':str(path),
        'before_image_path':str(before_path),'report_path':str(journal_path)}
    def save():
        # Atomic report replacement keeps a pending action readable after interruption.
        temporary=journal_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(journal_path)
    def read_header(report,stage):
        check_member_pause(should_stop)
        if fast_visible:
            from backup_ocr import apply_visible_header
            return apply_visible_header(report,expected_selected,name,stage)
        return header_reader(report) if header_reader is not None else report
    def make_plan(report):
        ledger=analyze_member_visual(report,name)['selected_numbers'] if fast_visible else expected_selected
        return plan_member_selection(report,name,ledger)
    save()
    try:
        check_member_pause(should_stop)
        # Old images must not be presented as evidence for a failed new attempt.
        path.unlink(missing_ok=True);before_path.unlink(missing_ok=True)
        for attempt in (1,2):path.with_name(path.stem+f'_check{attempt}.png').unlink(missing_ok=True)
        prepare={'mode':'prepare','image_path':str(before_path),'number':name}
        if expected_dialog is not None:prepare['expected_dialog_id']=expected_dialog['dialog_runtime_id']
        before=run_window_script(window,'select_member.ps1',prepare)
        result['before']=before
        if expected_dialog is not None and any(before.get(key)!=expected_dialog.get(key)
                for key in ('window_handle','process_id','dialog_runtime_id')):
            raise ValueError('批量执行期间目标弹窗已改变，停止，不点击')
        search=before.get('search_preparation') or {}
        if search.get('number')!=name or search.get('value_matches') is not True:
            raise ValueError('未确认搜索框已填写完整数字备注，停止选择')
        result['search_applied']=True
        if header_reader is not None or fast_visible:
            before=read_header(before,'before')
            result['before']=before
        plan=make_plan(before)
        result['plan']=plan
        evidence_path=Path(before['image_path']) if before.get('full_header_scan',{}).get('ok') else before_path
        result['before_image_path']=str(evidence_path)
        def evidence_digest():
            digest=hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            if before.get('full_header_scan') and before['full_header_scan'].get('final_image_sha256')!=digest:
                raise ValueError('完整读取后的截图文件改变，停止，不点击')
            return digest
        if header_reader is not None and not fast_visible and plan['action']=='click_once':
            # A slow OCR worker may outlive Telegram's search-result layout.
            # Refresh only before input, with a new capture and a complete replan.
            result['preflight_reads']=[]
            for attempt in range(1,4):
                check_member_pause(should_stop)
                payload={key:plan[key] for key in ('number','x','y','name_bounds')}
                payload.update(mode='check',before_image_path=str(evidence_path),
                    before_sha256=evidence_digest(),
                    capture=before['capture'],regions=before['regions'],dialog_runtime_id=before['dialog_runtime_id'],
                    guard_image_path=str(path.with_name(path.stem+f'_precheck{attempt}.png')))
                payload.update(member_header_guard(before))
                result['state']='checking_before_click';save()
                try:
                    guard=run_window_script(window,'select_member.ps1',payload)
                except WindowActionError as error:
                    guard=error.report
                    result['preflight_reads'].append({'attempt':attempt,'report':guard});save()
                    fresh=guard.get('guard_report') or {}
                    can_refresh=(guard.get('mode')=='check' and guard.get('read_only') is True
                        and guard.get('click_attempted') is False and guard.get('click_sent') is False
                        and guard.get('search_attempted') is False and guard.get('final_invite_clicked') is False
                        and guard.get('guard_stage') in ('layout','row')
                        and fresh.get('ok') is True and fresh.get('read_only') is True and fresh.get('scope')=='member_visual'
                        and all(before.get(key) is not None and before[key]==fresh.get(key)
                            for key in ('window_handle','process_id','dialog_runtime_id','capture')))
                    if not can_refresh:raise
                    if attempt==3:raise ValueError('点击前列表仍在变化，已停止；未发送联系人点击')
                    refresh_path=path.with_name(path.stem+f'_before_refresh{attempt}.png')
                    refresh=run_window_script(window,'inspect_member_visual.ps1',{'image_path':str(refresh_path)})
                    if any(before.get(key)!=refresh.get(key) for key in
                            ('window_handle','process_id','dialog_runtime_id','capture')):
                        raise ValueError('刷新证据时窗口或弹窗改变，停止，不点击')
                    before=read_header(refresh,'before')
                    plan=make_plan(before)
                    evidence_path=Path(before['image_path']) if before.get('full_header_scan',{}).get('ok') else refresh_path
                    result.update(before=before,plan=plan,before_image_path=str(evidence_path))
                    save()
                    if plan['action']=='already_selected':break
                    continue
                result['preflight_reads'].append({'attempt':attempt,'report':guard});save()
                if not (guard.get('mode')=='check' and guard.get('read_only') is True
                        and guard.get('guard_stable') is True and guard.get('click_attempted') is False
                        and guard.get('click_sent') is False and guard.get('final_invite_clicked') is False
                        and guard.get('number')==name and guard.get('before_sha256')==payload['before_sha256']):
                    raise ValueError('未确认点击前只读复查通过，停止，不点击')
                break
        if plan['action']=='already_selected':
            result.update(ok=True,state='already_selected',selection_verified=True,
                reason='顶部已存在该备注；没有再次点击，也未邀请')
            save();return result
        # Fast flow performs the same fresh image/UIA guard once inside
        # click_once, immediately before input, rather than duplicating it.
        payload={key:plan[key] for key in ('number','x','y','name_bounds')}
        payload.update(mode='click_once',before_image_path=str(evidence_path),
            before_sha256=evidence_digest(),
            capture=before['capture'],regions=before['regions'],dialog_runtime_id=before['dialog_runtime_id'])
        if header_reader is not None or fast_visible:
            payload['guard_image_path']=str(path.with_name(path.stem+'_click_guard.png'))
            payload.update(member_header_guard(before))
        result.update(state='click_requested',click_requested=True)
        save()  # Must succeed before any physical input.
        check_member_pause(should_stop)
        click=run_window_script(window,'select_member.ps1',payload)
        result['click']=click
        result['click_sent']=click.get('click_sent') is True
        if not result['click_sent']:raise RuntimeError('未确认点击发送，停止核查')
        result['state']='verifying';result['verification_reads']=[];save()
        consecutive=0
        for attempt in (range(1,2) if fast_visible else range(1,4)):
            check_member_pause(should_stop)
            time.sleep(0.2 if fast_visible else 0.5)
            read_path=path if fast_visible or attempt==3 else path.with_name(path.stem+f'_check{attempt}.png')
            # Repeat only read-only capture, never prepare/search/click. Keep
            # each image and the pending action journal until verification ends.
            after=run_window_script(window,'inspect_member_visual.ps1',{'image_path':str(read_path)})
            if header_reader is not None or fast_visible:after=read_header(after,'after')
            result['after']=after
            checked=(verify_visible_member_selection if fast_visible else verify_member_selection)(before,after,name,expected_selected)
            result['verification_reads'].append({'attempt':attempt,'image_path':str(read_path),
                'report':after,'selection_verified':checked['selection_verified']})
            save()
            if any(before.get(key)!=after.get(key) for key in
                   ('window_handle','process_id','dialog_runtime_id','capture')):
                raise ValueError('只读复查期间窗口或弹窗改变，停止核查')
            if checked['analysis']['other_header_words']:
                raise ValueError('只读复查发现其他文字备注，停止核查')
            consecutive=consecutive+1 if checked['selection_verified'] else 0
        result.update(checked)
        result['selection_verified']=consecutive>=(1 if fast_visible else 2)
        result['verification_mode']='visible_pair' if fast_visible else 'complete_header'
        result['ok']=result['selection_verified']
        result['reason']=('上一位及新增编号可见核对通过' if fast_visible else '连续两次只读核对通过，停在 Add 前') if result['ok'] else '只读复查未连续两次核对通过；停止，不自动再次点击，请查看 Telegram 和截图'
        result['state']='selected' if result['ok'] else 'review'
        save();return result
    except Exception as error:
        if isinstance(error,WindowActionError):result['action_error']=error.report
        result['state']='paused' if (isinstance(error,MemberSelectionPaused) or (should_stop is not None and should_stop())) else ('review' if result['click_requested'] else 'stopped_before_click')
        result['reason']=str(error)+'；不会自动再次点击，请查看当前 Telegram 页面。'
        save();return result


def fill_contact_test(window, phone, number, *, require_empty=False):
    from store import normalize
    phone=normalize(phone,'phone')
    name=numeric_label(number)
    payload={'phone':phone,'number':name}
    if require_empty:payload['require_empty']=True
    return run_window_script(window,'fill_contact.ps1',payload)


def verify_contact_profile(report, phone, number):
    """Require independent profile evidence; window titles never prove a saved contact."""
    from store import normalize
    phone=normalize(phone,'phone')
    name=numeric_label(number)
    checks={'profile':report.get('ok') is True and report.get('read_only') is True
            and report.get('scope')=='profile' and not report.get('truncated')
            and not report.get('errors'), 'phone':False, 'number':False,
            'edit_contact':False, 'delete_contact':False}
    # Report matches separately from completeness, so diagnostics stay accurate.
    # An incomplete/error report still cannot produce a verified result.
    if report.get('scope')=='profile' and report.get('ok') is True and report.get('read_only') is True:
        texts=[]
        buttons=[]
        for row in report.get('controls',[]):
            if row.get('visible') is not True:
                continue
            if row.get('type')=='Text':
                texts.extend(str(row.get('name') or '').splitlines())
            elif row.get('type') in ('Button','MenuItem') and row.get('enabled'):
                buttons.append(str(row.get('name') or '').strip())
        texts=[line.strip() for line in texts]
        checks['number']=name in texts
        for line in texts:
            # An entire label must be a phone, not a matching fragment of other text.
            if re.fullmatch(r'\+[0-9\s()\-]+',line):
                try:checks['phone'] |= normalize(line,'phone')==phone
                except ValueError:pass
        checks['edit_contact']=any(label in ('Edit contact','编辑联系人','Editar contato') for label in buttons)
        checks['delete_contact']=any(label in ('Delete contact','删除联系人','Apagar contato') for label in buttons)
    return {'verified':all(checks.values()),'checks':checks,'number':name}


def summarize(report):
    controls=report.get('controls',[])
    edits=[row for row in controls if row.get('type')=='Edit' and not row.get('offscreen')]
    buttons=[row for row in controls if row.get('type') in ('Button','MenuItem') and not row.get('offscreen')]
    choices=[row for row in controls if row.get('type') in ('CheckBox','ListItem') and row.get('visible')]
    scope=report.get('scope','window')
    lines=[f'只读报告：范围 {scope}；共 {len(controls)} 个控件；可见输入框 {len(edits)} 个，可见按钮／菜单项 {len(buttons)} 个。',
           '没有读取输入框的值，没有点击或修改界面。',
           '资料页范围会读取可见文本，用于核对联系人手机号和数字备注。' if scope=='profile' else
           ('弹窗范围保留联系人列表名称和选中状态，供适配选人页面。' if scope=='dialog' else '非资料页的文本名称已省略。'),
           '控件名称仍可能包含账号或联系人信息，分享前可检查报告。','']
    for row in edits+buttons+choices:
        state=f'  勾选：{row.get("toggle_state")}  选中：{row.get("selection_selected")}' if row.get('toggle_state') is not None or row.get('selection_selected') is not None else ''
        lines.append(f'{row["type"]}  名称：{row.get("name") or "（无名称）"}  ID：{row.get("automation_id") or "（无ID）"}  可用：{row.get("enabled")}  值模式：{row.get("value_pattern")}{state}')
    if scope in ('profile','dialog'):
        lines.append('\n资料页／弹窗可见文本：')
        lines.extend(str(row.get('name') or '') for row in controls if row.get('type')=='Text' and row.get('visible'))
    elif not edits:
        lines.append('\n未读取到可见输入框。请保持 New Contact 页面打开再检查；若仍没有，需要评估其他识别方式。')
    if report.get('truncated'):
        lines.append('\n报告达到数量或深度上限，可能不完整。')
    if report.get('errors'):
        lines.append('\n部分控件无法读取：'+str(report['errors'][0]))
    return '\n'.join(lines)
