# Read-only chat-list probe. Never clicks, scrolls, opens chats or reads edit values.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$report = [ordered]@{
    ok=$false; read_only=$true; scope='group_catalog_probe';
    all_groups_scanned=$false; account_identity_verified=$false;
    group_count=$null; stable_group_ids_available=$false;
    final_invite_clicked=$false; candidates=@(); errors=@(); truncated=$false
}
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $expectedPid = [Int32]::Parse($env:TG_INSPECT_PID)
    $handleValue = [Int64]::Parse($env:TG_INSPECT_HWND)
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    if ($null -eq $root -or $root.Current.ProcessId -ne $expectedPid -or $root.Current.ClassName -ne 'class MainWindow') {
        throw 'Bound Telegram window identity or main-window class changed.'
    }
    $report.process_id=$expectedPid
    $report.window_handle=$handleValue
    function Visible($element) {
        $c=$element.Current
        return ($c.ProcessId -eq $expectedPid -and -not $c.IsOffscreen -and $c.BoundingRectangle.Width -gt 0 -and $c.BoundingRectangle.Height -gt 0)
    }
    function FindClass([string]$name) {
        $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,$name)
        return @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object { Visible $_ })
    }
    $boxes=@(FindClass 'class Ui::BoxLayerWidget')
    if ($boxes.Count -gt 0) { throw 'Close Add Members, New Contact and other modal dialogs first; show the normal chat list.' }
    $batchScan=$false
    if ($env:TG_ACTION_PAYLOAD) {
        $payload=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
        $batchScan=($payload.batch_scan -eq $true)
    }
    if ($batchScan) {
        if (@(FindClass 'class Info::Profile::Widget').Count -gt 0) {
            throw 'Close the profile panel and return to the normal chat list before batch scanning.'
        }
        $dialogs=@(FindClass 'class Dialogs::Widget')
        if ($dialogs.Count -ne 1) { throw 'No unique normal chat-list panel for batch scanning.' }
        $editCondition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit)
        $searches=@($dialogs[0].FindAll([System.Windows.Automation.TreeScope]::Descendants,$editCondition) | Where-Object {
            (Visible $_) -and $_.Current.ClassName -eq 'class Ui::InputField' -and
            $_.Current.Name -in @('Search','Pesquisar','Buscar','搜索')
        })
        if ($searches.Count -ne 1) { throw 'Cannot uniquely identify the normal chat search field.' }
        $value=$null
        if (-not $searches[0].TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value)) {
            throw 'Chat search value is unavailable; cannot prove the list is unfiltered.'
        }
        if (-not [string]::IsNullOrEmpty(([System.Windows.Automation.ValuePattern]$value).Current.Value)) {
            throw 'Clear the main chat search before batch scanning. No search text was changed.'
        }
        $report.search_empty=$true
        $report.ordinary_chat_verified=$true
    }
    $lists=@(FindClass 'class Dialogs::InnerWidget')
    $walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
    if ($lists.Count -ne 1) {
        # Class/role topology only; do not fall back to the message-history tree.
        $queue=New-Object System.Collections.Queue
        $queue.Enqueue(@{element=$root;depth=0})
        $topology=New-Object System.Collections.Generic.List[object]
        while ($queue.Count -gt 0 -and $topology.Count -lt 300) {
            $entry=$queue.Dequeue();$element=$entry.element;$c=$element.Current
            if ($c.ProcessId -ne $expectedPid) { continue }
            $topology.Add([pscustomobject]@{class_name=$c.ClassName;type=$c.ControlType.ProgrammaticName;depth=$entry.depth;visible=(Visible $element)})
            if ($entry.depth -ge 8) { continue }
            $child=$walker.GetFirstChild($element)
            while ($null -ne $child) {
                if ($queue.Count -ge 300) { break }
                $queue.Enqueue(@{element=$child;depth=$entry.depth+1})
                $child=$walker.GetNextSibling($child)
            }
        }
        $report.class_topology=@($topology.ToArray())
        throw 'No unique supported chat-list container. The saved class-only report is needed to adapt this Telegram version.'
    }
    $list=$lists[0]
    $report.list_class=$list.Current.ClassName
    $report.list_runtime_id=$list.GetRuntimeId() -join ','
    $report.list_bounds=@{left=$list.Current.BoundingRectangle.Left;top=$list.Current.BoundingRectangle.Top;width=$list.Current.BoundingRectangle.Width;height=$list.Current.BoundingRectangle.Height}
    if ($batchScan) {
        $rowCondition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem)
        $firstRows=@($list.FindAll([System.Windows.Automation.TreeScope]::Children,$rowCondition))
        if ($firstRows.Count -lt 2 -or -not (Visible $firstRows[0]) -or -not (Visible $firstRows[1])) {
            throw 'The first two chat rows must both be visible. Scroll the normal chat list to the top.'
        }
        $report.first_two_visible=$true
    }
    $scroll=$null
    $report.scroll_pattern=$list.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern,[ref]$scroll)
    $queue=New-Object System.Collections.Queue
    $queue.Enqueue(@{element=$list;depth=0})
    $rows=New-Object System.Collections.Generic.List[object]
    $errors=New-Object System.Collections.Generic.List[string]
    $visited=0
    while ($queue.Count -gt 0 -and $visited -lt 2000) {
        $entry=$queue.Dequeue();$element=$entry.element;$visited++
        try {
            $c=$element.Current
            if ($c.ProcessId -ne $expectedPid) { continue }
            $type=$c.ControlType.ProgrammaticName.Replace('ControlType.','')
            if ($type -in @('ListItem','DataItem','TreeItem')) {
                $name=[string]$c.Name
                $help=[string]$c.HelpText
                $fieldNames=New-Object System.Collections.Generic.List[string]
                if ($type -eq 'ListItem') {
                    $field=$walker.GetFirstChild($element)
                    $fieldCount=0
                    while ($null -ne $field) {
                        $fieldCount++
                        if ($fieldCount -gt 32) { $report.truncated=$true; break }
                        if ($field.Current.ProcessId -ne $expectedPid) { throw 'Chat field belongs to a different process.' }
                        if ($field.Current.ControlType -eq [System.Windows.Automation.ControlType]::DataItem) {
                            $fieldNames.Add([string]$field.Current.Name)
                        }
                        $field=$walker.GetNextSibling($field)
                    }
                }
                $rows.Add([pscustomobject]@{
                    type=$type;class_name=$c.ClassName;automation_id=$c.AutomationId;
                    runtime_id=($element.GetRuntimeId() -join ',');visible=(Visible $element);
                    name=$name.Substring(0,[Math]::Min(512,$name.Length));
                    help_text=$help.Substring(0,[Math]::Min(512,$help.Length));
                    label_truncated=($name.Length -gt 512 -or $help.Length -gt 512);
                    group_type_confirmed=$false;stable_group_id=$null
                    child_field_names=@($fieldNames.ToArray())
                })
            }
            # Capture direct ListItem fields above; do not count their DataItems
            # as chats or descend into their message-preview fields.
            if ($type -eq 'ListItem') { continue }
            $child=$walker.GetFirstChild($element)
            if ($entry.depth -lt 32) {
                while ($null -ne $child) {
                    if (($queue.Count + $visited) -ge 2000) { $report.truncated=$true; break }
                    $queue.Enqueue(@{element=$child;depth=$entry.depth+1})
                    $child=$walker.GetNextSibling($child)
                }
            } elseif ($null -ne $child) { $report.truncated=$true }
        } catch { $errors.Add($_.Exception.Message) }
    }
    if ($queue.Count -gt 0) { $report.truncated=$true }
    $report.ok=$true
    $report.visited=$visited
    $report.observed_chat_rows=@($rows.ToArray() | Where-Object { $_.type -eq 'ListItem' }).Count
    $report.candidates=@($rows.ToArray())
    $report.errors=@($errors.ToArray())
    $report.reason='Chat ListItems and their direct field names captured. Runtime IDs are temporary UI identifiers, not Telegram group IDs. The account-wide group count is unverified.'
    $report | ConvertTo-Json -Depth 8 -Compress
} catch {
    $report.error=$_.Exception.Message
    $report | ConvertTo-Json -Depth 8 -Compress
    return
}
