# Open an empty New Contact form in one bound instance. Never fill or submit.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$report=[ordered]@{ok=$false;scope='contact_form_open';mode='open_only';state='review';stage='initial';
    launch_requested=$false;launch_started=$false;fields_written=$false;create_clicked=$false;
    contact_created=$false;contact_database_updated=$false;final_invite_clicked=$false;
    empty_form_verified=$false;field_schema_verified=$false;errors=@()}
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $payload=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
    $handleValue=[Int64]::Parse($env:TG_INSPECT_HWND)
    $expectedPid=[Int32]::Parse($env:TG_INSPECT_PID)
    $expectedPath=[IO.Path]::GetFullPath([string]$payload.executable_path)
    if ([IO.Path]::GetFileName($expectedPath) -ine 'Telegram.exe' -or -not [IO.File]::Exists($expectedPath)) {
        throw 'The bound executable is not an existing Telegram.exe.'
    }
    $report.window_handle=$handleValue;$report.process_id=$expectedPid
    $report.executable_path=$expectedPath
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    if ($null -eq $root -or $root.Current.ProcessId -ne $expectedPid -or $root.Current.ClassName -ne 'class MainWindow') {
        throw 'Bound Telegram window identity changed. Rescan first.'
    }
    $rootId=$root.GetRuntimeId() -join ','
    $report.main_runtime_id=$rootId
    $walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
    function Visible($element) {
        $c=$element.Current;$b=$c.BoundingRectangle
        return ($c.ProcessId -eq $expectedPid -and -not $c.IsOffscreen -and $b.Width -gt 0 -and $b.Height -gt 0)
    }
    function FindClass($parent,[string]$name) {
        $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,$name)
        return @($parent.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object { Visible $_ })
    }
    function AssertRoot {
        $now=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
        if ($null -eq $now -or $now.Current.ProcessId -ne $expectedPid -or $now.Current.ClassName -ne 'class MainWindow' -or
            ($now.GetRuntimeId() -join ',') -ne $rootId -or -not (Visible $now)) {
            throw 'Bound window changed or became unavailable. No automatic retry.'
        }
    }
    function AssertProcess {
        $process=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = '+$expectedPid)
        if ($null -eq $process -or [string]::IsNullOrEmpty($process.ExecutablePath) -or
            -not [string]::Equals([IO.Path]::GetFullPath($process.ExecutablePath),$expectedPath,[StringComparison]::OrdinalIgnoreCase)) {
            throw 'Executable path does not match the bound running process.'
        }
        if ([string]::IsNullOrWhiteSpace($process.CommandLine)) { throw 'Cannot verify the running process launch options.' }
        if ([string]$process.CommandLine -match '(?i)(?:^|\s|\")-(?:many|workdir)(?=\s|\"|$)') {
            throw 'Custom -workdir or -many launch needs separate routing verification; no new process was launched.'
        }
        # Never export command-line contents, which can include private options.
        $report.process_path_verified=$true
        $report.default_instance_route_verified=$true
    }
    function EmptyContact($contact) {
        $scope=$walker.GetParent($contact)
        if ($null -eq $scope -or $scope.Current.ClassName -ne 'class Ui::BoxLayerWidget' -or -not (Visible $scope)) {
            throw 'Unexpected New Contact container.'
        }
        $boxes=@(FindClass $root 'class Ui::BoxLayerWidget')
        if ($boxes.Count -ne 1 -or ($boxes[0].GetRuntimeId() -join ',') -ne ($scope.GetRuntimeId() -join ',')) {
            throw 'Multiple dialogs are present; existing forms were preserved.'
        }
        $all=@($scope.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))
        function Field([string[]]$names,[string]$kind) {
            $fields=@($all | Where-Object { (Visible $_) -and $_.Current.IsEnabled -and
                $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and $_.Current.Name -in $names })
            if ($kind -eq 'phone') { $fields=@($fields | Where-Object { $_.Current.ClassName -eq 'class Ui::PhoneInput' }) }
            else {
                $inner=@($fields | Where-Object { $_.Current.ClassName -eq 'class Ui::InputField::Inner' })
                if ($inner.Count -gt 0) { $fields=$inner }
                else { $fields=@($fields | Where-Object { $_.Current.ClassName -eq 'class Ui::InputField' }) }
            }
            if ($fields.Count -ne 1) { throw ('Missing or ambiguous contact field: '+$kind) }
            $value=$null
            if (-not $fields[0].TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value) -or
                $value -isnot [System.Windows.Automation.ValuePattern] -or $value.Current.IsReadOnly) {
                throw ('Cannot read the editable contact field: '+$kind)
            }
            return [string]$value.Current.Value
        }
        $first=Field @('First name','名字','名','Nome') 'first_name'
        $last=Field @('Last name','姓氏','姓','Sobrenome') 'last_name'
        $phone=Field @('Phone Number','手机号','电话号码','Número de Telefone') 'phone'
        $phone=[regex]::Replace($phone,'[\s()\-]','')
        $create=@($all | Where-Object { (Visible $_) -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
            $_.Current.Name -in @('Create','创建','Criar') })
        if ($create.Count -ne 1) { throw 'Cannot uniquely identify the contact Create button.' }
        if (-not [string]::IsNullOrWhiteSpace($first) -or -not [string]::IsNullOrWhiteSpace($last) -or
            $phone -notmatch '^\+?[0-9]{0,3}$') {
            throw 'New Contact already contains user data; it was not cleared, filled or submitted.'
        }
        AssertRoot
        $report.dialog_runtime_id=$scope.GetRuntimeId() -join ','
        $report.contact_runtime_id=$contact.GetRuntimeId() -join ','
        $report.field_schema_verified=$true;$report.empty_form_verified=$true
    }
    AssertRoot
    AssertProcess
    $windows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$expectedPid)) |
        Where-Object { (Visible $_) -and $_.Current.ClassName -eq 'class MainWindow' })
    if ($windows.Count -ne 1 -or ($windows[0].GetRuntimeId() -join ',') -ne $rootId) {
        throw 'This process has multiple main windows; account routing is ambiguous.'
    }
    $contacts=@(FindClass $root 'class AddContactBox')
    if ($contacts.Count -gt 1) { throw 'Multiple New Contact forms are visible.' }
    if ($contacts.Count -eq 1) {
        EmptyContact $contacts[0]
        $report.state='existing_empty';$report.stage='verified';$report.ok=$true
    } else {
        if (@(FindClass $root 'class Ui::BoxLayerWidget').Count -gt 0 -or
            @(FindClass $root 'class Info::Profile::Widget').Count -gt 0) {
            throw 'Close existing dialogs and the profile panel before this opening test.'
        }
        $report.stage='launch'
        AssertRoot
        AssertProcess
        $report.launch_requested=$true
        # Exact executable + closed arguments. Never ShellExecute a global tg: URI.
        Start-Process -FilePath $expectedPath -ArgumentList @('--','tg://contacts/new') -WorkingDirectory ([IO.Path]::GetDirectoryName($expectedPath)) | Out-Null
        $report.launch_started=$true
        $report.stage='wait_for_form'
        $contact=$null
        for ($i=0;$i -lt 40;$i++) {
            Start-Sleep -Milliseconds 200
            AssertRoot
            $contacts=@(FindClass $root 'class AddContactBox')
            if ($contacts.Count -gt 1) { throw 'Multiple New Contact forms appeared.' }
            if ($contacts.Count -eq 1) { $contact=$contacts[0];break }
        }
        if ($null -eq $contact) { throw 'New Contact did not appear in the bound window. Check Telegram manually; no retry.' }
        AssertProcess
        EmptyContact $contact
        $report.state='opened_empty';$report.stage='verified';$report.ok=$true
    }
    $report | ConvertTo-Json -Depth 6 -Compress
} catch {
    $report.error=$_.Exception.Message
    $report | ConvertTo-Json -Depth 6 -Compress
    exit 1
}
