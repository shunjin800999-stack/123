# Submit one exactly matched filled form once; open Info only. Never invite or send.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$report=[ordered]@{ok=$false;scope='contact_submit';state='review';stage='initial';
    fields_written=$false;create_attempted=$false;create_invoked=$false;profile_open_attempted=$false;
    profile_open_invoked=$false;profile_opened=$false;profile_already_open=$false;result_page_verified=$false;contact_created_verified=$false;
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
    function GetContactResultState($boxes,$forms,$profiles,$bars,$titleMatches,$sameFormResult,$newDialog) {
        if ($boxes -eq 1 -and ($sameFormResult -or $newDialog)) { return 'result_dialog' }
        if ($boxes -ne 0 -or $forms -ne 0 -or -not $titleMatches) { return 'transition' }
        if ($profiles -eq 1) { return 'profile' }
        if ($profiles -eq 0 -and $bars -eq 1) { return 'chat' }
        return 'transition'
    }
    function ReadContactResultPage {
        $pageRoot=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
        $boxes=@(FindClass $pageRoot 'class Ui::BoxLayerWidget')
        $forms=@(FindClass $pageRoot 'class AddContactBox')
        $profiles=@(FindClass $pageRoot 'class Info::Profile::Widget')
        $bars=@(FindClass $pageRoot 'class HistoryView::TopBarWidget')
        $title=([string]$pageRoot.Current.Name).Trim([char[]]@([char]0x200E,[char]0x200F,[char]0x202A,[char]0x202C,[char]0x2066,[char]0x2069))
        $titleMatches=$title -cmatch $titlePattern
        $sameFormResult=$false;$newDialog=$false;$resultId=$null
        if ($boxes.Count -eq 1) {
            $resultId=$boxes[0].GetRuntimeId() -join ','
            $newDialog=$resultId -ne $report.dialog_runtime_id
            if (-not $newDialog -and $forms.Count -eq 1) {
                # Telegram can reuse the original box for its lookup result.
                $rows=@($boxes[0].FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object { Visible $_ })
                $retry=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name -in @('Try someone else','Tentar outro') -and $_.Current.ClassName -eq 'class Ui::RoundButton' })
                $edits=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit })
                $sameFormResult=$retry.Count -eq 1 -and $edits.Count -eq 0 -and @(FindClass $boxes[0] 'class AddContactBox').Count -eq 1
            }
        }
        $profileIds=@($profiles | ForEach-Object { $_.GetRuntimeId() -join ',' })
        $dialogIds=@($boxes | ForEach-Object { $_.GetRuntimeId() -join ',' })
        $state=GetContactResultState $boxes.Count $forms.Count $profiles.Count $bars.Count $titleMatches $sameFormResult $newDialog
        return [PSCustomObject]@{state=$state;root=$pageRoot;profiles=$profiles;result_id=$resultId;
            summary=[ordered]@{state=$state;title=$title;title_matches=$titleMatches;dialog_count=$boxes.Count;
                contact_form_count=$forms.Count;profile_count=$profiles.Count;chat_bar_count=$bars.Count;
                dialog_runtime_ids=$dialogIds;profile_runtime_ids=$profileIds}}
    }
    function RecordContactResultReadError($message) {
        if ($report.result_read_errors.Count -lt 8) {
            $report.result_read_errors+=@([ordered]@{iteration=$report.result_page_reads;message=$message})
        }
    }
    function WaitContactResultPage([bool]$allowChat,[int]$limitMs=10000) {
        while ($resultClock.ElapsedMilliseconds -lt $limitMs) {
            # A different window/process is never a recoverable animation.
            AssertRoot
            try {
                $page=ReadContactResultPage
                $report.result_page_reads++
                $report.result_wait_ms=$resultClock.ElapsedMilliseconds
                $report.last_result_page=$page.summary
                $signature=$page.summary | ConvertTo-Json -Depth 4 -Compress
                $history=@($report.result_page_history)
                if ($history.Count -lt 12 -and ($history.Count -eq 0 -or
                        $signature -ne ($history[-1] | ConvertTo-Json -Depth 4 -Compress))) {
                    $report.result_page_history+=@($page.summary)
                }
                if ($page.state -eq 'profile' -or ($allowChat -and $page.state -in @('chat','result_dialog'))) {
                    return $page
                }
            } catch { RecordContactResultReadError $_.Exception.Message }
            Start-Sleep -Milliseconds 200
        }
        throw 'Create result page did not settle in time; inspect last_result_page. No resubmission.'
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
    # Read/navigation retries never repeat Create. Normal ready pages return immediately.
    $titlePattern='^'+[regex]::Escape($number)+'(?: – \([0-9]+\))?$'
    $resultClock=[Diagnostics.Stopwatch]::StartNew()
    $report.result_read_errors=@();$report.result_page_history=@();$report.result_page_reads=0
    $report.result_wait_ms=0;$report.last_result_page=$null
    $page=$null;$pattern=$null
    while ($true) {
        $page=WaitContactResultPage $true
        if ($page.state -ne 'chat') { break }
        AssertRoot;AssertProcess
        try {
            $root=$page.root
            $bar=Single @(FindClass $root 'class HistoryView::TopBarWidget') 'resulting chat top bar'
            $buttons=@($bar.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)) |
                Where-Object { (Visible $_) -and $_.Current.IsEnabled -and $_.Current.ClassName -eq 'class Ui::IconButton' -and $_.Current.Name -in @('Info','Informações','Informação','信息','资料') })
            $info=Single $buttons 'contact Info navigation button';$pattern=$null
            if (-not $info.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern) -or
                $pattern -isnot [System.Windows.Automation.InvokePattern]) { throw 'No supported Info InvokePattern.' }
            # The page may still change between the first chat read and finding Info.
            # Return to the same bounded wait instead of treating that transition as failure.
            $fresh=ReadContactResultPage
            $report.last_result_page=$fresh.summary
            if ($fresh.state -ne 'chat') { continue }
            $freshBar=Single @(FindClass $fresh.root 'class HistoryView::TopBarWidget') 'current resulting chat top bar'
            if (($freshBar.GetRuntimeId() -join ',') -ne ($bar.GetRuntimeId() -join ',')) { continue }
            break
        } catch { RecordContactResultReadError $_.Exception.Message }
        Start-Sleep -Milliseconds 200
    }
    if ($page.state -eq 'chat') {
        $report.title_verified=$true;$report.stage='open_profile';$report.profile_open_attempted=$true
        ([System.Windows.Automation.InvokePattern]$pattern).Invoke();$report.profile_open_invoked=$true
        # Info is invoked once. Re-read through a temporary layer or partial profile.
        $report.result_wait_before_info_ms=$resultClock.ElapsedMilliseconds
        $resultClock=[Diagnostics.Stopwatch]::StartNew()
        $page=WaitContactResultPage $false 4000
    } elseif ($page.state -eq 'profile') {
        $report.profile_already_open=$true
    }
    AssertRoot;AssertProcess
    if ($page.state -eq 'result_dialog') {
        $report.result_runtime_id=$page.result_id
        $report.ok=$true;$report.state='result_dialog';$report.stage='submitted'
    } else {
        $profile=Single $page.profiles 'resulting contact profile'
        $report.title_verified=$true;$report.result_page_verified=$true
        $report.profile_runtime_id=$profile.GetRuntimeId() -join ','
        $report.profile_opened=$true;$report.state='profile_opened';$report.stage='submitted';$report.ok=$true
    }
    # Navigation never implies success; Python still verifies the bound phone and remark.
    $report | ConvertTo-Json -Depth 6 -Compress
} catch {
    $report.error=$_.Exception.Message
    $report | ConvertTo-Json -Depth 6 -Compress
    exit 1
}
