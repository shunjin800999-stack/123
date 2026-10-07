# Private, serialized window worker. Contact inspection/verified profile close allowed; no contact creation or final Add actions.
$ErrorActionPreference='Stop'
[Console]::InputEncoding=New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$workerScripts=@{}
foreach ($workerName in @('select_member.ps1','inspect_member_visual.ps1','inspect_groups.ps1','prepare_group_page.ps1','navigate_group.ps1','inspect_controls.ps1','close_contact_profile.ps1')) {
    $workerScripts[$workerName]=[ScriptBlock]::Create([IO.File]::ReadAllText(
        (Join-Path $env:TG_ASSISTANT_DIR $workerName),[Text.Encoding]::UTF8))
}
while ($null -ne ($workerLine=[Console]::ReadLine())) {
    $workerRequest=$null;$workerOutput='';$workerError='';$workerCode=0
    $workerTimer=[Diagnostics.Stopwatch]::StartNew()
    try {
        $workerRequest=$workerLine | ConvertFrom-Json
        if ($null -eq $workerRequest -or -not $workerScripts.ContainsKey([string]$workerRequest.script)) {
            throw 'Unsupported member operation.'
        }
        # Never keep the previous account, window, or payload.
        $env:TG_INSPECT_HWND=[string]$workerRequest.hwnd
        $env:TG_INSPECT_PID=[string]$workerRequest.pid
        $env:TG_ACTION_PAYLOAD=$workerRequest.payload | ConvertTo-Json -Depth 40 -Compress
        # Child scope discards UIA nodes and coordinates on every request;
        # process-global compiled C# helpers alone remain reusable.
        $workerOutput=((& $workerScripts[[string]$workerRequest.script]) | Out-String).Trim()
    } catch {
        $workerCode=1;$workerError=$_.Exception.Message
    } finally {
        Remove-Item Env:TG_INSPECT_HWND -ErrorAction SilentlyContinue
        Remove-Item Env:TG_INSPECT_PID -ErrorAction SilentlyContinue
        Remove-Item Env:TG_ACTION_PAYLOAD -ErrorAction SilentlyContinue
    }
    $workerTimer.Stop()
    $workerEnvelope=@{request_id=$workerRequest.request_id;stdout=$workerOutput;stderr=$workerError;
        returncode=$workerCode;native_seconds=$workerTimer.Elapsed.TotalSeconds}
    [Console]::WriteLine(($workerEnvelope | ConvertTo-Json -Depth 4 -Compress))
}
