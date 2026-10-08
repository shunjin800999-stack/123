# Single-record test. It only sets three fields and NEVER invokes a button.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$changed = New-Object System.Collections.Generic.List[string]
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $payload = $env:TG_ACTION_PAYLOAD | ConvertFrom-Json
    $phone = [string]$payload.phone
    $number = [string]$payload.number
    if ($phone -notmatch '^\+[1-9][0-9]{6,14}$') { throw 'Invalid international phone number.' }
    if ($number -notmatch '^[0-9]{1,6}$' -or [int]$number -lt 0) { throw 'Invalid numeric contact name.' }
    $expectedPid = [Int32]::Parse($env:TG_INSPECT_PID)
    $handleValue = [Int64]::Parse($env:TG_INSPECT_HWND)
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    if ($null -eq $root -or $root.Current.ProcessId -ne $expectedPid) { throw 'Window identity changed. Rescan first.' }
    if ($root.Current.ClassName -ne 'class MainWindow') { throw 'Unsupported Telegram main window.' }
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    function GetContactDialog {
        $condition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty, 'class AddContactBox')
        $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
        $visible = @($all | Where-Object { -not $_.Current.IsOffscreen -and $_.Current.IsEnabled -and $_.Current.ProcessId -eq $expectedPid -and $_.Current.BoundingRectangle.Width -gt 0 -and $_.Current.BoundingRectangle.Height -gt 0 })
        if ($visible.Count -ne 1) { throw 'Exactly one visible New Contact dialog is required. Open an empty contact form first.' }
        return $visible[0]
    }
    $dialog = GetContactDialog
    $dialogId = $dialog.GetRuntimeId() -join ','
    $scope = $walker.GetParent($dialog)
    if ($null -eq $scope -or $scope.Current.ClassName -ne 'class Ui::BoxLayerWidget') { throw 'Unexpected contact dialog container.' }
    $all = $scope.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    function FindField([string[]]$names, [string]$kind) {
        $fields = @($all | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and $_.Current.Name -in $names -and -not $_.Current.IsOffscreen -and $_.Current.ProcessId -eq $expectedPid })
        if ($kind -eq 'phone') {
            $fields = @($fields | Where-Object { $_.Current.ClassName -eq 'class Ui::PhoneInput' })
        } else {
            $inner = @($fields | Where-Object { $_.Current.ClassName -eq 'class Ui::InputField::Inner' })
            if ($inner.Count -gt 0) { $fields = $inner }
            else { $fields = @($fields | Where-Object { $_.Current.ClassName -eq 'class Ui::InputField' }) }
        }
        if ($fields.Count -ne 1) { throw ('Missing or ambiguous contact field: ' + $kind) }
        $element = $fields[0]
        if (-not $element.Current.IsEnabled) { throw ('Contact field is disabled: ' + $kind) }
        $pattern = $null
        if (-not $element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) { throw ('No writable ValuePattern for: ' + $kind) }
        if ($pattern -isnot [System.Windows.Automation.ValuePattern] -or $pattern.Current.IsReadOnly) { throw ('Contact field is read-only: ' + $kind) }
        return @{element=$element; pattern=$pattern}
    }
    $first = FindField @('First name','名字','名','Nome') 'first_name'
    $last = FindField @('Last name','姓氏','姓','Sobrenome') 'last_name'
    $phoneField = FindField @('Phone Number','手机号','电话号码','Número de Telefone') 'phone'
    $create = @($all | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.Name -in @('Create','创建','Criar') -and -not $_.Current.IsOffscreen -and $_.Current.ProcessId -eq $expectedPid })
    if ($create.Count -ne 1) { throw 'The contact Create button could not be uniquely identified.' }
    function AssertDialog {
        $current = GetContactDialog
        if (($current.GetRuntimeId() -join ',') -ne $dialogId) { throw 'The contact dialog changed during the test.' }
    }
    # All fields are validated before the first write. Recheck dialog identity each time.
    AssertDialog
    if ($payload.require_empty -eq $true) {
        $initialPhone = [regex]::Replace([string]$phoneField.pattern.Current.Value, '[\s()\-]', '')
        if (-not [string]::IsNullOrWhiteSpace($first.pattern.Current.Value) -or
            -not [string]::IsNullOrWhiteSpace($last.pattern.Current.Value) -or
            $initialPhone -notmatch '^\+?[0-9]{0,3}$') {
            throw 'Queue fill requires an empty contact form (phone country prefix only). Existing content was not overwritten.'
        }
    }
    $phoneField.pattern.SetValue($phone)
    $changed.Add('phone')
    AssertDialog
    $first.pattern.SetValue($number)
    $changed.Add('first_name')
    AssertDialog
    $last.pattern.SetValue('')
    $changed.Add('last_name')
    AssertDialog
    $actualPhone = [regex]::Replace([string]$phoneField.pattern.Current.Value, '[\s()\-]', '')
    if ($actualPhone -ne $phone -or $first.pattern.Current.Value -ne $number -or $last.pattern.Current.Value -ne '') { throw 'Field read-back did not match the requested values. Review the form manually.' }
    [ordered]@{ok=$true;mode='fill_only';number=$number;phone_matches=$true;empty_guard=($payload.require_empty -eq $true);window_handle=$handleValue;process_id=$expectedPid;main_runtime_id=($root.GetRuntimeId() -join ',');contact_runtime_id=$dialogId;contact_created=$false;final_invite_clicked=$false;changed_fields=@($changed.ToArray())} | ConvertTo-Json -Compress
} catch {
    [ordered]@{ok=$false;error=$_.Exception.Message;mode='fill_only';contact_created=$false;final_invite_clicked=$false;changed_fields=@($changed.ToArray())} | ConvertTo-Json -Compress
    exit 1
}
