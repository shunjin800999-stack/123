# Submit one exactly matched filled form once; open Info only. Never invite or send.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$report=[ordered]@{ok=$false;scope='contact_submit';state='review';stage='initial';
    fields_written=$false;create_attempted=$false;create_invoked=$false;profile_open_attempted=$false;
    profile_open_invoked=$false;profile_opened=$false;contact_created_verified=$false;
    contact_database_updated=$false;final_invite_clicked=$false;errors=@()}
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $payload=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
    $phone=[string]$payload.phone;$number=[string]$payload.number
    if ($phone -notmatch '^\+[1-9][0-9]{6,14}$' -or $number -notmatch '^[1-9][0-9]{0,5}$') { throw 'Invalid phone or numeric remark.' }
    $handleValue=[Int64]::Parse($env:TG_INSPECT_HWND);$expectedPid=[Int32]::Parse($env:TG_INSPECT_PID)
    $report.window_handle=$handleValue;$report.process_id=$expectedPid;$report.number=$number
    $expectedPath=[IO.Path]::GetFullPath([string]$payload.executable_path)
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    $rootId=[string]$payload.main_runtime_id;$contactId=[string]$payload.contact_runtime_id
    if ([string]::IsNullOrWhiteSpace($rootId) -or [string]::IsNullOrWhiteSpace($contactId)) { throw 'Missing identity from the preceding fill.' }
    $report.main_runtime_id=$rootId;$report.contact_runtime_id=$contactId
    $walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
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
            ($now.GetRuntimeId() -join ',') -ne $rootId -or -not (Visible $now)) { throw 'The filled form window changed.' }
    }
    function AssertProcess {
        $process=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = '+$expectedPid)
        if ($null -eq $process -or [string]::IsNullOrEmpty($process.ExecutablePath) -or
            -not [string]::Equals([IO.Path]::GetFullPath($process.ExecutablePath),$expectedPath,[StringComparison]::OrdinalIgnoreCase)) { throw 'The bound executable path changed.' }
        $report.process_path_verified=$true
    }
    function MatchingForm {
        AssertRoot
        $contact=Single @(FindClass $root 'class AddContactBox') 'filled New Contact'
        if (($contact.GetRuntimeId() -join ',') -ne $contactId) { throw 'The filled contact form instance changed.' }
        $box=$walker.GetParent($contact)
        if ($null -eq $box -or $box.Current.ClassName -ne 'class Ui::BoxLayerWidget') { throw 'Unexpected form container.' }
        $boxes=@(FindClass $root 'class Ui::BoxLayerWidget')
        $report.dialog_runtime_id=$box.GetRuntimeId() -join ','
        if ($boxes.Count -ne 1 -or ($boxes[0].GetRuntimeId() -join ',') -ne ($box.GetRuntimeId() -join ',')) { throw 'Multiple dialogs are present.' }
        $all=@($contact.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))
        function ReadField($names,[string]$kind) {
            $fields=@($all | Where-Object { (Visible $_) -and $_.Current.IsEnabled -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and $_.Current.Name -in $names })
            if ($kind -eq 'phone') { $fields=@($fields | Where-Object { $_.Current.ClassName -eq 'class Ui::PhoneInput' }) }
            else {
                $inner=@($fields | Where-Object { $_.Current.ClassName -eq 'class Ui::InputField::Inner' })
                if ($inner.Count -gt 0) { $fields=$inner } else { $fields=@($fields | Where-Object { $_.Current.ClassName -eq 'class Ui::InputField' }) }
            }
            $field=Single $fields $kind;$value=$null
            if (-not $field.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value) -or
                $value -isnot [System.Windows.Automation.ValuePattern] -or $value.Current.IsReadOnly) { throw ('Cannot verify '+$kind+'.') }
            return [string]$value.Current.Value
        }
        $first=ReadField @('First name','名字','名','Nome') 'first_name'
        $last=ReadField @('Last name','姓氏','姓','Sobrenome') 'last_name'
        $actualPhone=[regex]::Replace((ReadField @('Phone Number','手机号','电话号码','Número de Telefone') 'phone'),'[\s()\-]','')
        if ($first -cne $number -or $last -cne '' -or $actualPhone -cne $phone) { throw 'Filled phone/name changed; Create was not invoked.' }
        # Create belongs to the BoxLayer wrapper, as in the verified fill helper.
        $buttons=@($box.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)) |
            Where-Object { (Visible $_) -and $_.Current.IsEnabled -and $_.Current.Name -in @('Create','Criar','创建') })
        return (Single $buttons 'contact Create button')
    }
    AssertRoot;AssertProcess
    if (@(FindClass $root 'class Info::Profile::Widget').Count -gt 0) { throw 'A profile is already open beneath the contact form.' }
    $report.stage='match_filled_form'
    $create=MatchingForm;$buttonId=$create.GetRuntimeId() -join ','
    Start-Sleep -Milliseconds 300
    $create=MatchingForm
    if (($create.GetRuntimeId() -join ',') -ne $buttonId) { throw 'Create button instance changed.' }
    $report.fields_verified=$true;$report.create_runtime_id=$buttonId
    $pattern=$null
    if (-not $create.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern) -or
        $pattern -isnot [System.Windows.Automation.InvokePattern]) { throw 'No supported Create InvokePattern; no keyboard/coordinate fallback.' }
    AssertRoot;AssertProcess
    $create=MatchingForm
    if (($create.GetRuntimeId() -join ',') -ne $buttonId) { throw 'Create button changed before invocation.' }
    $report.stage='create';$report.create_attempted=$true
    ([System.Windows.Automation.InvokePattern]$pattern).Invoke()
    $report.create_invoked=$true;$report.stage='wait_result'
    # Never invoke Create twice. Read for up to ten seconds, including pending network response.
    $ready=$false;$report.result_read_errors=@()
    for ($i=0;$i -lt 50;$i++) {
        Start-Sleep -Milliseconds 200;AssertRoot
        $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
        try {
        # This result replaces the fields inside the original AddContactBox;
        # it does not necessarily create a new BoxLayer runtime identity.
        $old=@(FindClass $root 'class Ui::BoxLayerWidget' | Where-Object { ($_.GetRuntimeId() -join ',') -eq $report.dialog_runtime_id })
        if ($old.Count -eq 1) {
            $rows=@($old[0].FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object { Visible $_ })
            $retry=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name -in @('Try someone else','Tentar outro') -and $_.Current.ClassName -eq 'class Ui::RoundButton' })
            $edits=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit })
            if ($retry.Count -eq 1 -and $edits.Count -eq 0 -and @(FindClass $old[0] 'class AddContactBox').Count -eq 1) {
                $report.result_runtime_id=$report.dialog_runtime_id
                $report.ok=$true;$report.state='result_dialog';$report.stage='submitted';$ready=$true;break
            }
        }
        $boxes=@(FindClass $root 'class Ui::BoxLayerWidget' | Where-Object { ($_.GetRuntimeId() -join ',') -ne $report.dialog_runtime_id })
        if ($boxes.Count -gt 0) {
            $report.result_runtime_id=(Single $boxes 'result dialog').GetRuntimeId() -join ','
            $report.ok=$true;$report.state='result_dialog';$report.stage='submitted';$ready=$true;break
        }
        if (@(FindClass $root 'class AddContactBox').Count -gt 0) { continue }
        $bars=@(FindClass $root 'class HistoryView::TopBarWidget')
        # The title is a navigation guard only, never contact-success evidence.
        $title=([string]$root.Current.Name).Trim([char[]]@([char]0x200E,[char]0x200F,[char]0x202A,[char]0x202C,[char]0x2066,[char]0x2069))
        $titlePattern='^'+[regex]::Escape($number)+'(?: – \([0-9]+\))?$'
        if ($bars.Count -eq 1 -and $title -cmatch $titlePattern) { $ready=$true;break }
        } catch {
            # Retry only reading after Create, within the original bounded loop.
            # Window identity checks remain outside this catch; no input is retried.
            if ($report.result_read_errors.Count -lt 8) { $report.result_read_errors+=@([ordered]@{iteration=$i;message=$_.Exception.Message}) }
            continue
        }
    }
    if (-not $ready) { throw 'Create result was not verified in time; inspect the actual page. No resubmission.' }
    if ($report.state -ne 'result_dialog') {
        AssertRoot;AssertProcess
        if (@(FindClass $root 'class Ui::BoxLayerWidget').Count -gt 0 -or @(FindClass $root 'class Info::Profile::Widget').Count -gt 0) { throw 'Page changed before opening the resulting contact profile.' }
        $bar=Single @(FindClass $root 'class HistoryView::TopBarWidget') 'resulting chat top bar'
        $buttons=@($bar.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)) |
            Where-Object { (Visible $_) -and $_.Current.IsEnabled -and $_.Current.ClassName -eq 'class Ui::IconButton' -and $_.Current.Name -in @('Info','Informações','Informação','信息','资料') })
        $info=Single $buttons 'contact Info navigation button';$pattern=$null
        if (-not $info.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern) -or
            $pattern -isnot [System.Windows.Automation.InvokePattern]) { throw 'No supported Info InvokePattern.' }
        $currentTitle=([string]$root.Current.Name).Trim([char[]]@([char]0x200E,[char]0x200F,[char]0x202A,[char]0x202C,[char]0x2066,[char]0x2069))
        if ($currentTitle -cnotmatch $titlePattern) { throw 'Resulting chat changed before opening Info.' }
        $report.title_verified=$true;$report.stage='open_profile';$report.profile_open_attempted=$true
        ([System.Windows.Automation.InvokePattern]$pattern).Invoke();$report.profile_open_invoked=$true
        $profile=$null
        for ($i=0;$i -lt 20;$i++) {
            Start-Sleep -Milliseconds 200;AssertRoot
            if (@(FindClass $root 'class Ui::BoxLayerWidget').Count -gt 0) { throw 'A dialog appeared while opening the profile.' }
            $profiles=@(FindClass $root 'class Info::Profile::Widget')
            if ($profiles.Count -gt 1) { throw 'Multiple profiles are visible.' }
            if ($profiles.Count -eq 1) { $profile=$profiles[0];break }
        }
        if ($null -eq $profile) { throw 'Resulting profile did not open; no retry.' }
        $report.profile_runtime_id=$profile.GetRuntimeId() -join ','
        $report.profile_opened=$true;$report.state='profile_opened';$report.stage='submitted';$report.ok=$true
    }
    # Profile existence/closed dialog never implies success; Python reads it twice.
    $report | ConvertTo-Json -Depth 6 -Compress
} catch {
    $report.error=$_.Exception.Message
    $report | ConvertTo-Json -Depth 6 -Compress
    exit 1
}
