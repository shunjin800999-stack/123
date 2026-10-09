"""Task outcome audio runs independently of window automation and UI polling."""
import base64
import os
import queue
import subprocess
import threading

PHRASES={'stopped':'已停止，请检查','finished':'执行完毕，请确认'}


def play_notification(kind):
    if kind not in PHRASES:raise ValueError('未知提示音')
    if os.name!='nt':return
    # EncodedCommand avoids console encoding differences on Windows PowerShell.
    script="""$ErrorActionPreference='Stop'
try { [System.Media.SystemSounds]::Asterisk.Play() } catch {}
Start-Sleep -Milliseconds 200
$voice=New-Object -ComObject SAPI.SpVoice
$tokens=@($voice.GetVoices())
$chinese=@($tokens | Where-Object { $_.GetAttribute('Language') -match '(^|;)0?(804|404|c04|1004|1404)(;|$)' })
if ($chinese.Count -gt 0) { $voice.Voice=$chinese[0] }
[void]$voice.Speak('"""+PHRASES[kind]+"')"
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    powershell=os.path.join(os.environ.get('SystemRoot',r'C:\Windows'),'System32','WindowsPowerShell','v1.0','powershell.exe')
    try:
        subprocess.run([powershell,'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=20,check=True)
    except (OSError,subprocess.SubprocessError):
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except (ImportError,OSError,RuntimeError):pass


class TaskNotifier:
    def __init__(self,player=play_notification):
        self._player=player;self._armed=set();self._pending=queue.Queue();self._worker=None

    def arm_addition(self,queue_id):
        self._armed.add(queue_id)

    def disarm_addition(self,queue_id):
        self._armed.discard(queue_id)

    def observe_addition(self,snapshot):
        if not snapshot:return
        queue_id=snapshot['id'];state=snapshot['state']
        if state=='running':self.arm_addition(queue_id);return
        if queue_id not in self._armed or state not in ('review','stopped','done'):return
        complete=state=='done' and all(j['state'] in ('target_reached','skipped_account') for j in snapshot['jobs'])
        self.finish_addition(queue_id,complete)

    def finish_addition(self,queue_id,complete):
        if queue_id not in self._armed:return
        self._armed.discard(queue_id);self.emit('finished' if complete else 'stopped')

    def emit(self,kind):
        if kind not in PHRASES:raise ValueError('未知提示音')
        self._pending.put(kind)
        if self._worker is None:
            self._worker=threading.Thread(target=self._run,daemon=True);self._worker.start()

    def _run(self):
        while True:
            kind=self._pending.get()
            try:self._player(kind)
            except Exception:pass  # Audio must never stop a contact operation.
            finally:self._pending.task_done()
