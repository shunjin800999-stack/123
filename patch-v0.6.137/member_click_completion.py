"""Separate confirmed target clicks from visual proof of member selection."""
from visual_members import numeric_label, member_labels, plan_member_selection


def target_header_recognition_failed(report, number):
    """Only the existing bound, missing/ambiguous header OCR permits a retry."""
    proof=report.get('selection_header_ocr') or {}
    backup=report.get('backup_ocr') or {}
    error=proof.get('error')
    return (report.get('ok') is True and report.get('read_only') is True
        and report.get('scope')=='member_visual' and report.get('header_scan_only') is True
        and report.get('final_invite_clicked') is False and backup.get('ok') is True
        and backup.get('target_member_only') is True and backup.get('target_stage')=='after'
        and proof.get('ok') is False and isinstance(error,str)
        and any(error.endswith(message) for message in
            ('本次目标备注未确认选中，停止，不重复点击','备用顶部仍有无法完整核对的标签候选'))
        and report.get('target_only_checkpoint')=={'number':number,'stage':'after'})


def confirmed_target_click(step):
    """Recheck the matched row and native single-click receipt, without new OCR."""
    try:
        name=numeric_label(step['number']);before=step['before'];click=step['click']
        ledger=member_labels(step['expected_selected'],allow_empty=True)
        if (step.get('scope')!='single_member_selection' or name in ledger
                or step.get('target_member_only') is not True
                or any(step.get(k) is not True for k in
                    ('search_requested','search_applied','click_requested','click_sent'))
                or step.get('final_invite_clicked') is not False
                or before.get('target_only_checkpoint')!={'number':name,'stage':'before'}
                or (before.get('header_scroll') or {}).get('search_value')!=name
                or (before.get('search_preparation') or {}).get('number')!=name
                or (before.get('search_preparation') or {}).get('value_matches') is not True
                or click.get('ok') is not True or click.get('mode')!='click_once'
                or click.get('number')!=name or click.get('click_attempted') is not True
                or click.get('click_sent') is not True or click.get('final_invite_clicked') is not False):
            return False
        plan=plan_member_selection(before,name,[])
        return (plan['action']=='click_once'
            and all(step['plan'].get(k)==plan[k] for k in ('action','number','x','y','name_bounds'))
            and all(click.get(k)==plan[k] for k in ('x','y')))
    except (KeyError,TypeError,ValueError,AttributeError):
        return False


def can_continue_after_header_failure(step):
    """Both original read and one fresh read failed; never click a second time."""
    try:
        if not confirmed_target_click(step):return False
        before=step['before'];reads=step['verification_reads'];retry=step['post_click_retry']
        if (step.get('selection_verified') is not False or len(reads)!=2
                or retry.get('capture_requested') is not True or retry.get('verified') is not False):
            return False
        for attempt,read in enumerate(reads,1):
            report=read['report']
            if (read.get('attempt')!=attempt or read.get('selection_verified') is not False
                    or not target_header_recognition_failed(report,step['number'])
                    or any(before.get(k) is None or before[k]!=report.get(k) for k in
                        ('window_handle','process_id','dialog_runtime_id','capture','scale'))):
                return False
        return step.get('after')==reads[-1]['report']
    except (KeyError,TypeError,ValueError,AttributeError):
        return False
