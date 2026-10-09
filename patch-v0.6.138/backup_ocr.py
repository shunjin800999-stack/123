"""Isolated OCR: diagnostic captures and bound header/list evidence for selection."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import atexit
import queue
import threading
import uuid

from controls_probe import inspect_member_visual
from visual_members import analyze_member_visual

FRAME_KEYS=('window_handle','process_id','dialog_runtime_id','capture','regions','scale')


def require_environment():
    base=Path(__file__).resolve().parent
    python=base/'.ocr-env'/'Scripts'/'python.exe'
    if not python.is_file():raise ValueError('未找到备用识别环境；请在已经安装成功的原文件夹运行 START.bat，首次使用才运行 SETUP_OCR.bat。')
    return base,python


class OCRSession:
    """Serialized worker: fresh request binding, bounded reads, no stale reuse."""
    def __init__(self):
        self.process=None
        self.lock=threading.Lock()
        self.responses=None

    def close(self):
        process=self.process;self.process=None
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin,process.stdout):
                if stream is not None:stream.close()

    def start(self):
        base,python=require_environment()
        env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONIOENCODING']='utf-8'
        self.process=subprocess.Popen([str(python),str(base/'ocr_worker.py'),'--serve'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            env=env,text=True,encoding='utf-8',bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.responses=queue.Queue()
        process=self.process;responses=self.responses
        def read():
            try:
                for line in process.stdout:responses.put(line)
            except (OSError,ValueError):pass
            finally:responses.put(None)
        threading.Thread(target=read,daemon=True).start()

    def request(self,report):
        with self.lock:
            try:
                if self.process is None or self.process.poll() is not None:
                    self.close();self.start()
                token=uuid.uuid4().hex
                self.process.stdin.write(json.dumps({'request_id':token,'report':report},ensure_ascii=False)+'\n')
                self.process.stdin.flush()
                line=self.responses.get(timeout=90)
                if line is None:raise ValueError('识别程序已退出')
                result=json.loads(line)
                if not isinstance(result,dict) or result.get('request_id')!=token:
                    raise ValueError('识别响应与当前请求不一致')
                if result.get('ok') is not True:self.close()
                return result
            except Exception as error:
                self.close()
                return {'ok':False,'error':str(error),'used_for_selection':False,
                    'selection_verified':False,'final_invite_clicked':False}


_SESSION=OCRSession()
atexit.register(_SESSION.close)


def run_backup_ocr(report):
    return _SESSION.request(report)


def save_report(report,image_path):
    path=Path(image_path).resolve().with_suffix('.json')
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def apply_selection_header(report):
    """Analyze this exact existing capture; never recapture, search or click."""
    report=copy.deepcopy(report)
    report['selection_header_ocr']={'ok':False,'used_for_selection':False}
    try:
        if report.get('ok') is not True or report.get('read_only') is not True or report.get('scope')!='member_visual':
            raise ValueError('备用顶部核对仅接受只读弹窗截图')
        frame={key:copy.deepcopy(report[key]) for key in FRAME_KEYS}
        if not frame['dialog_runtime_id']:raise ValueError('缺少实时弹窗身份')
        path=Path(report['image_path'])
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        secondary=run_backup_ocr(report)
        report['backup_ocr']=secondary
        if secondary.get('ok') is not True:raise ValueError(secondary.get('error','备用识别失败'))
        if report.get('progressive_member_ocr') is True and not isinstance(secondary.get('member_row_scan'),dict):
            raise ValueError('逐行识别程序未返回行范围，请关闭助手后完整覆盖补丁')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest or secondary.get('image_sha256')!=digest:
            raise ValueError('备用识别截图校验不一致')
        report['selection_header_ocr']={'ok':True,'used_for_selection':True,
            'image_sha256':digest,'frame':frame}
        # Check bound evidence before any caller can request a physical click.
        analysis=analyze_member_visual(report,'1')
        if not analysis['usable']:raise ValueError(analysis['reason'])
    except Exception as error:
        report['selection_header_ocr']={'ok':False,'used_for_selection':False,'error':str(error)}
    return report


def inspect_selection_visual(window,image_path,number):
    require_environment()  # Missing installation must fail before window activity.
    from selection_header_scan import read_current_header
    report=read_current_header(window,inspect_member_visual(window,image_path,number))
    report['analysis']=analyze_member_visual(report,number)
    save_report(report,image_path)
    return report


def apply_visible_header(report,ledger,number,stage):
    """Bound the current bottom view only; never call the scroll reader."""
    from visual_members import member_labels,numeric_label
    from selection_header_scan import page_area
    report=copy.deepcopy(report)
    report.pop('full_header_scan',None)
    report['header_page']=True
    report['selection_list_policy']='first_matching_numeric_row'
    if report.get('first_row_scan') is True:
        if stage=='before':
            report['progressive_member_ocr']=True
            report['member_row_target']=numeric_label(number)
            report.pop('header_scan_only',None)
        else:
            report['header_scan_only']=True
            report.pop('progressive_member_ocr',None)
            report.pop('member_row_target',None)
    report['regions']['header_ocr']=page_area(report,minimum_height=24)
    report['visible_header_checkpoint']={'scope':'visible_pair','ledger':member_labels(ledger,allow_empty=True),
        'number':numeric_label(number),'stage':stage}
    return apply_selection_header(report)


def apply_target_header(report,number,stage):
    """Row OCR before input; only the newest selected numeric chip after input."""
    from visual_members import numeric_label
    from selection_header_scan import page_area
    if stage not in ('before','after'):raise ValueError('本次目标核验阶段无效')
    report=copy.deepcopy(report)
    for key in ('full_header_scan','visible_header_checkpoint'):
        report.pop(key,None)
    report.update(header_page=True,target_member_only=True,
        selection_list_policy='first_matching_numeric_row',
        target_only_checkpoint={'number':numeric_label(number),'stage':stage})
    report['regions']['header_ocr']=page_area(report,minimum_height=24)
    if stage=='before':
        report.update(progressive_member_ocr=True,member_row_target=numeric_label(number))
        report.pop('header_scan_only',None)
    else:
        report['header_scan_only']=True
        report.pop('progressive_member_ocr',None);report.pop('member_row_target',None)
    return apply_selection_header(report)


def inspect_backup_ocr(window,image_path):
    require_environment()
    report=inspect_member_visual(window,image_path,'1')
    report['backup_ocr']=run_backup_ocr(report)
    save_report(report,image_path)
    return report
