# Only Close panel on an exactly matched saved contact. No Esc, generic Close or Add.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$report=[ordered]@{ok=$false;scope='contact_profile_close';state='review';stage='initial';
    close_attempted=$false;close_invoked=$false;profile_absent=$false;modal_absent=$false;
    fields_written=$false;create_attempted=$false;contact_database_updated=$false;final_invite_clicked=$false;errors=@()}
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $payload=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
    $phone=[string]$payload.phone;$number=[string]$payload.number;$profileId=[string]$payload.profile_runtime_id
    if ($phone -notmatch '^\+[1-9][0-9]{6,14}$' -or $number -notmatch '^[1-9][0-9]{0,5}$' -or [string]::IsNullOrWhiteSpace($profileId)) { throw 'Missing saved contact identity.' }
    $handleValue=[Int64]::Parse($env:TG_INSPECT_HWND);$expectedPid=[Int32]::Parse($env:TG_INSPECT_PID)
    $expectedPath=[IO.Path]::GetFullPath([string]$payload.executable_path)
    $report.window_handle=$handleValue;$report.process_id=$expectedPid;$report.number=$number
    $report.profile_runtime_id=$profileId;$report.executable_path=$expectedPath
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    if ($null -eq $root -or $root.Current.ProcessId -ne $expectedPid -or $root.Current.ClassName -ne 'class MainWindow') { throw 'Bound Telegram window identity changed.' }
    $rootId=$root.GetRuntimeId() -join ',';$report.main_runtime_id=$rootId
    function Visible($element) {
        $c=$element.Current;$b=$c.BoundingRectangle
        return ($c.ProcessId -eq $expectedPid -and -not $c.IsOffscreen -and $b.Width -gt 0 -and $b.Height -gt 0)
    }
    function FindClass($parent,[string]$name) {
        $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,$name)
        return @($parent.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object { Visible $_ })
    }
    function Single($elements,[string]$description) {
        $items=@($elements);if ($items.Count -ne 1) { throw ('Expected exactly one '+$description+'.') };return $items[0]
    }
    function AssertRoot {
        $now=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
        if ($null -eq $now -or $now.Current.ProcessId -ne $expectedPid -or $now.Current.ClassName -ne 'class MainWindow' -or
            ($now.GetRuntimeId() -join ',') -ne $rootId -or -not (Visible $now)) { throw 'Window changed during profile closing.' }
    }
    function AssertProcess {
        $process=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = '+$expectedPid)
        if ($null -eq $process -or [string]::IsNullOrEmpty($process.ExecutablePath) -or
            -not [string]::Equals([IO.Path]::GetFullPath($process.ExecutablePath),$expectedPath,[StringComparison]::OrdinalIgnoreCase)) { throw 'Bound running executable changed.' }
        $report.process_path_verified=$true
    }
    function MatchProfile {
        AssertRoot
        if (@(FindClass $root 'class Ui::BoxLayerWidget').Count -gt 0) { throw 'A dialog is present; its contents were preserved.' }
        $profile=Single @(FindClass $root 'class Info::Profile::Widget') 'saved contact profile'
        if (($profile.GetRuntimeId() -join ',') -ne $profileId) { throw 'Previously verified profile instance changed.' }
        $all=@($profile.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object { Visible $_ })
        $names=@($all | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and
            $_.Current.ClassName -eq 'class Ui::MarqueeLabel' -and $_.Current.Name -ceq $number })
        if ($names.Count -ne 1) { throw 'Profile numeric remark does not match the last saved contact.' }
        $phones=@($all | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and
            [regex]::Replace([string]$_.Current.Name,'[\s()\-]','') -ceq $phone })
        if ($phones.Count -ne 1) { throw 'Profile full phone does not match the saved contact.' }
        foreach ($labels in @(@('Edit contact','Editar contato','编辑联系人'),@('Delete contact','Apagar contato','删除联系人'))) {
            $buttons=@($all | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name -in $labels })
            if ($buttons.Count -ne 1) { throw 'Saved contact Edit/Delete markers are unavailable.' }
        }
        $bars=@(FindClass $profile 'class Info::Profile::TopBar')
        $bar=Single $bars 'contact profile top bar'
        $close=@($bar.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)) |
            Where-Object { (Visible $_) -and $_.Current.IsEnabled -and $_.Current.Name -in @('Close panel','Fechar painel') -and
                $_.Current.ClassName -eq 'class Info::Profile::`anonymous namespace''::BackdropIconButton' })
        return (Single $close 'Close panel button')
    }
    AssertRoot;AssertProcess;$report.stage='match_profile'
    # One fresh full identity check immediately before Close; no repeated scans or sleep.
    $close=MatchProfile;$closeId=$close.GetRuntimeId() -join ','
    $report.profile_fields_verified=$true;$report.close_runtime_id=$closeId
    $pattern=$null
    if (-not $close.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern) -or $pattern -isnot [System.Windows.Automation.InvokePattern]) { throw 'No supported Close panel InvokePattern; no coordinate or keyboard fallback.' }
    AssertRoot
    if (($close.GetRuntimeId() -join ',') -ne $closeId -or -not (Visible $close) -or -not $close.Current.IsEnabled) { throw 'Close panel changed before invocation.' }
    $report.stage='close_profile';$report.close_attempted=$true
    ([System.Windows.Automation.InvokePattern]$pattern).Invoke();$report.close_invoked=$true
    $report.stage='wait_closed'
    for ($i=0;$i -lt 20;$i++) {
        Start-Sleep -Milliseconds 200;AssertRoot
        if (@(FindClass $root 'class Ui::BoxLayerWidget').Count -gt 0) { throw 'A dialog appeared; it was not dismissed.' }
        if (@(FindClass $root 'class Info::Profile::Widget').Count -eq 0) {
            $report.profile_absent=$true;$report.modal_absent=$true;break
        }
    }
    if (-not $report.profile_absent) { throw 'Profile closing was not verified; no retry.' }
    AssertRoot;AssertProcess
    $report.ok=$true;$report.state='closed';$report.stage='verified'
    $report | ConvertTo-Json -Depth 6 -Compress
} catch {
    $report.error=$_.Exception.Message
    $report | ConvertTo-Json -Depth 6 -Compress
    return
}
