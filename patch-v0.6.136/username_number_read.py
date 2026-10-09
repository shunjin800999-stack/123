"""Bounded read-only retries for the numeric title after an existing Done."""
import time

from controls_probe import WindowActionError
from username_contact import profile_number_verified


def read_username_number(window, number, read, *, sleep=None, clock=None):
    sleep=sleep or time.sleep
    clock=clock or time.monotonic
    started=clock();profile_id=None;history=[];latest=None;outcome='budget_exhausted'
    for attempt in range(11):
        if attempt and clock()-started>=3:
            break
        report=read()
        if not isinstance(report,dict):
            raise ValueError('数字备注核验未返回有效控件报告')
        latest=dict(report)
        bound=(report.get('ok') is True and report.get('read_only') is True and
            report.get('window_handle')==window['hwnd'] and report.get('process_id')==window['pid'] and
            bool(report.get('scope_runtime_id')))
        scope=report.get('scope');runtime_id=report.get('scope_runtime_id')
        titles=[c.get('name') for c in report.get('controls',[]) if c.get('visible') is True and
            c.get('type')=='Text' and c.get('class_name')=='class Ui::MarqueeLabel']
        history.append({'attempt':attempt+1,'scope':scope,'runtime_id':runtime_id,
            'titles':titles,'truncated':report.get('truncated'),'errors':list(report.get('errors') or [])[:8]})
        if bound and scope=='profile' and report.get('scope_class')=='class Info::Profile::Widget':
            if profile_id is not None and runtime_id!=profile_id:
                outcome='profile_changed'
                latest['number_read_retry']={'attempts':len(history),'outcome':outcome,'history':history}
                raise WindowActionError('数字核验期间资料页身份改变；未重复Done，保留原任务',latest)
            profile_id=runtime_id
            if profile_number_verified(report,window,number):
                outcome='verified';break
        elif not (bound and profile_id is None and scope=='window' and
                  report.get('scope_class')=='class MainWindow'):
            outcome='not_retryable';break
        remaining=3-(clock()-started)
        if remaining<=0 or attempt==10:
            break
        sleep(min(.3,remaining))
    latest['number_read_retry']={'attempts':len(history),'interval_ms':300,'budget_ms':3000,
        'elapsed_ms':round((clock()-started)*1000),'outcome':outcome,'history':history}
    return latest
