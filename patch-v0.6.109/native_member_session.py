"""Bound window PowerShell session. Failures terminate, never retry input."""
import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

class NativeMemberSession:
    def __init__(self, *, username=False):
        self.username=username
        self.worker_name='native_username_worker.ps1' if username else 'native_member_worker.ps1'
        self.process=None;self.responses=None;self.lock=threading.Lock()

    def close(self):
        process=self.process;self.process=None
        if process is not None:
            if process.poll() is None:process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin,process.stdout):
                if stream is not None:stream.close()

    def start(self,powershell,env):
        worker=Path(__file__).with_name(self.worker_name)
        # -Command string reads UTF-8 script explicitly; Windows PS5 -File
        # interprets BOM-less UTF-8 differently on some localized computers.
        env=dict(env,TG_SESSION_WORKER=self.worker_name)
        bootstrap="& ([ScriptBlock]::Create([IO.File]::ReadAllText((Join-Path $env:TG_ASSISTANT_DIR $env:TG_SESSION_WORKER),[Text.Encoding]::UTF8)))"
        self.process=subprocess.Popen([str(powershell),'-NoProfile','-NonInteractive','-STA','-Command',bootstrap],
            env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,encoding='utf-8',bufsize=1,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        process=self.process;self.responses=queue.Queue();responses=self.responses
        def read():
            try:
                for line in process.stdout:responses.put(line)
            except (OSError,ValueError):pass
            finally:responses.put(None)
        threading.Thread(target=read,daemon=True).start()

    def run(self,powershell,env,script,payload,timeout):
        allowed=('username_contact.ps1',) if self.username else ('select_member.ps1','inspect_member_visual.ps1','inspect_groups.ps1','prepare_group_page.ps1','navigate_group.ps1','inspect_controls.ps1','close_contact_profile.ps1')
        if script not in allowed:
            raise ValueError('常驻组件只允许群页面整理、导航、搜索选人、只读检查和已核验资料关闭')
        with self.lock:
            begin=time.monotonic();reused=self.process is not None and self.process.poll() is None
            try:
                if not reused:self.close();self.start(powershell,env)
                token=uuid.uuid4().hex
                request={'request_id':token,'script':script,'payload':payload or {},
                    'hwnd':env['TG_INSPECT_HWND'],'pid':env['TG_INSPECT_PID']}
                self.process.stdin.write(json.dumps(request,ensure_ascii=True)+'\n');self.process.stdin.flush()
                try:line=self.responses.get(timeout=timeout)
                except queue.Empty:raise subprocess.TimeoutExpired(script,timeout) from None
                if line is None:raise RuntimeError('窗口操作组件已退出；当前操作结果未确认，不自动重试')
                response=json.loads(line)
                if not isinstance(response,dict) or response.get('request_id')!=token:
                    raise ValueError('窗口操作响应与当前请求不一致；不自动重试')
                if not isinstance(response.get('stdout'),str) or not isinstance(response.get('stderr'),str) or response.get('returncode') not in (0,1):
                    raise ValueError('窗口操作响应格式无效')
                completed=subprocess.CompletedProcess([],response['returncode'],
                    stdout=response['stdout'].encode('utf-8'),stderr=response['stderr'].encode('utf-8'))
                completed.member_timing={'transport':'persistent_username_worker' if self.username else 'persistent_member_worker','reused':reused,
                    'seconds':round(time.monotonic()-begin,3),'native_seconds':response.get('native_seconds'),
                    'worker_pid':self.process.pid}
                # An invalid/native-failed response cannot carry state forward.
                report=json.loads(response['stdout']) if response['stdout'] else {}
                if response['returncode'] or report.get('ok') is not True:self.close()
                return completed
            except Exception:
                self.close();raise

SESSION=NativeMemberSession()
USERNAME_SESSION=NativeMemberSession(username=True)
atexit.register(SESSION.close)
atexit.register(USERNAME_SESSION.close)
