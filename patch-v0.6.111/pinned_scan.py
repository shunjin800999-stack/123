"""Detached, read-only batch discovery. Saving belongs to the UI thread.

Executable paths suggest stable labels, never prove Telegram account identity.
The user reviews all rows before a second live read and one atomic save.
"""
import copy
from collections import Counter
from pathlib import PureWindowsPath
import time
import uuid

from controls_probe import run_window_script
from groups import valid_window
from pinned_groups import consistent_pair
from group_navigation import same_observed_targets


def path_key(window):
    path = window.get('path')
    return str(PureWindowsPath(path)).casefold() if isinstance(path, str) and path else ''


def scan_entries(windows, known):
    windows = copy.deepcopy([w for w in windows if w.get('candidate') is True])
    if not 1 <= len(windows) <= 80:
        raise ValueError('需要扫描到 1–80 个 Telegram 窗口；先登录并打开各账号')
    paths = Counter(path_key(w) for w in windows)
    handles = Counter(w.get('hwnd') for w in windows)
    used = {r['account'] for r in known}
    jobs = []
    for index, window in enumerate(windows, 1):
        matches = {r['account'] for r in known if path_key(r['window']) == path_key(window)}
        account = next(iter(matches)) if len(matches) == 1 else ''
        reason = ''
        if len(matches) > 1:
            reason = '同一程序路径对应多个旧账号，请核查绑定'
        elif paths[path_key(window)] != 1 or handles[window.get('hwnd')] != 1:
            reason = '同一程序有多个窗口，无法确认独立账号，请只保留该账号的主窗口'
        elif not valid_window(window):
            reason = '窗口已最小化或身份信息不完整，请还原后重新扫描'
        if not account:
            number = 1
            while f'账号{number}' in used:
                number += 1
            account = f'账号{number}'
            used.add(account)
        jobs.append({'index': index, 'account': account, 'existing_account': len(matches) == 1,
            'window': window, 'state': 'review' if reason else 'not_started',
            'reason': reason, 'observation': None})
    names=Counter(j['account'] for j in jobs)
    for job in jobs:
        if names[job['account']]!=1:
            job.update(state='review',reason='同一旧账号对应多个程序路径，请核查原绑定')
    return jobs


def _read_pair(window, reader, pause):
    first = reader(window, 'inspect_groups.ps1', {'batch_scan': True})
    pause(0.5)
    second = reader(window, 'inspect_groups.ps1', {'batch_scan': True})
    if any(r.get(k) is not True for r in (first, second)
            for k in ('ordinary_chat_verified', 'search_empty', 'first_two_visible')):
        raise ValueError('未确认普通聊天列表、搜索为空和前两个条目可见，请关闭弹窗／资料面板并回到列表顶部')
    return consistent_pair(first, second, window)


def scan_pinned_windows(windows, known, progress=None, *, reader=None, pause=None, prepare=None):
    reader = reader or run_window_script
    pause = pause or time.sleep
    jobs = scan_entries(windows, known)
    for job in jobs:
        if job['state'] == 'review':
            continue
        if progress:
            progress(f'{job["index"]}/{len(jobs)} · {job["account"]}：读取两个置顶群')
        try:
            if prepare:job['preparation']=prepare(job['window'])
            job['observation'] = _read_pair(job['window'], reader, pause)
            job['state'] = 'ready'
            job['reason'] = '待你核对并确认'
        except Exception as error:
            job.update(state='review', reason=str(error))
    return {'scope': 'pinned_group_scan_preview', 'scan_id': uuid.uuid4().hex,
        'read_only': prepare is None, 'ok': all(j['state'] == 'ready' for j in jobs),
        'jobs': jobs, 'saved_accounts': [], 'contact_database_updated': False,
        'final_invite_clicked': False, 'account_identity_verified': False}


def recheck_pinned_preview(preview, indices, progress=None, *, reader=None, pause=None):
    """Re-read selected rows; any change rejects the whole selected save."""
    if (not isinstance(preview, dict) or preview.get('scope') != 'pinned_group_scan_preview'
            or preview.get('read_only') is not True or not preview.get('scan_id')
            or not isinstance(preview.get('jobs'), list)):
        raise ValueError('先扫描全部窗口及置顶群，并核对列表')
    indices = list(indices)
    if not indices or any(type(i) is not int for i in indices) or len(set(indices)) != len(indices):
        raise ValueError('请选择需要确认的账号行；失败行不能确认')
    jobs = [copy.deepcopy(j) for j in preview['jobs'] if j['index'] in indices]
    if (len(jobs) != len(indices) or len({j['account'] for j in jobs}) != len(jobs)
            or any(j['state'] != 'ready' or not j.get('observation') for j in jobs)):
        raise ValueError('所选行未识别成功或账号备注重复，请核对后重新扫描')
    reader = reader or run_window_script
    pause = pause or time.sleep
    records = []
    for job in jobs:
        if progress:
            progress(f'{job["account"]}：确认前复核窗口和置顶群')
        pair = _read_pair(job['window'], reader, pause)
        if not same_observed_targets(job['observation'], pair):
            raise ValueError(f'{job["account"]} 的群、顺序或控件已变化，本次全部未保存，请重新扫描核对')
        pair.update(account=job['account'], window=copy.deepcopy(job['window']))
        records.append(pair)
    return records
