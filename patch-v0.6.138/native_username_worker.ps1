# Dedicated serialized contact worker. Each request gets fresh UIA state.
$ErrorActionPreference='Stop'
[Console]::InputEncoding=New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$actionScript=[ScriptBlock]::Create([IO.File]::ReadAllText((Join-Path $env:TG_ASSISTANT_DIR 'username_contact.ps1'),[Text.Encoding]::UTF8))
while ($null -ne ($workerLine=[Console]::ReadLine())) {
 $workerRequest=$null;$workerOutput='';$workerError='';$workerCode=0
 $workerTimer=[Diagnostics.Stopwatch]::StartNew()
 try {
  $workerRequest=$workerLine | ConvertFrom-Json
  if ($null -eq $workerRequest -or $workerRequest.script -ne 'username_contact.ps1') { throw 'Unsupported username operation.' }
  $env:TG_INSPECT_HWND=[string]$workerRequest.hwnd
  $env:TG_INSPECT_PID=[string]$workerRequest.pid
  $env:TG_ACTION_PAYLOAD=$workerRequest.payload | ConvertTo-Json -Depth 40 -Compress
  $workerOutput=((& $actionScript) | Out-String).Trim()
 } catch { $workerCode=1;$workerError=$_.Exception.Message }
 finally {
  Remove-Item Env:TG_INSPECT_HWND,Env:TG_INSPECT_PID,Env:TG_ACTION_PAYLOAD -ErrorAction SilentlyContinue
  Remove-Variable -Name first,last,note,done,keyboardReady -Scope Script -ErrorAction SilentlyContinue
 }
 $workerTimer.Stop()
 [Console]::WriteLine((@{request_id=$workerRequest.request_id;stdout=$workerOutput;stderr=$workerError;returncode=$workerCode;native_seconds=$workerTimer.Elapsed.TotalSeconds} | ConvertTo-Json -Depth 4 -Compress))
}
