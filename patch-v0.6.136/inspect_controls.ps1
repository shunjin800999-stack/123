# Read-only diagnostic. Never invokes, clicks, focuses or changes a control.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $handleValue = [Int64]::Parse($env:TG_INSPECT_HWND)
    $expectedPid = [Int32]::Parse($env:TG_INSPECT_PID)
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    if ($null -eq $root) { throw 'No window automation element was returned.' }
    if ($root.Current.ProcessId -ne $expectedPid) { throw 'Window identity changed. Rescan first.' }
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    function IsVisible($element) {
        $c = $element.Current
        $b = $c.BoundingRectangle
        return ($c.ProcessId -eq $expectedPid -and -not $c.IsOffscreen -and
            -not [double]::IsInfinity($b.Left) -and -not [double]::IsInfinity($b.Top) -and
            -not [double]::IsNaN($b.Left) -and -not [double]::IsNaN($b.Top) -and
            $b.Width -gt 0 -and $b.Height -gt 0 -and
            -not [double]::IsInfinity($b.Width) -and -not [double]::IsInfinity($b.Height))
    }
    function VisibleClass([string]$className) {
        $condition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ClassNameProperty, $className)
        $found = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
        foreach ($element in $found) {
            if (IsVisible $element) { $element }
        }
    }
    function ReadProfileNumberReport($profile) {
        # Only read the bound profile's numeric-title candidates. Do not walk
        # unrelated biography/business widgets or query their patterns.
        $profileId=$profile.GetRuntimeId() -join ','
        $numberRows=New-Object System.Collections.Generic.List[object]
        $numberErrors=New-Object System.Collections.Generic.List[string]
        $numberRows.Add([pscustomobject]@{node=0;parent=-1;depth=0;type='Group';name='';
            class_name='class Info::Profile::Widget';visible=$true})
        try {
            $condition=[System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::ClassNameProperty,'class Ui::MarqueeLabel')
            $titles=@($profile.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition))
            foreach ($title in $titles) {
                try {
                    $info=$title.Current
                    if ($info.ProcessId -ne $expectedPid -or $info.ControlType -ne [System.Windows.Automation.ControlType]::Text -or
                            -not (IsVisible $title)) { continue }
                    $titleName=[string]$info.Name
                    if ([string]::IsNullOrWhiteSpace($titleName)) { throw 'Profile numeric title is not readable yet.' }
                    $numberRows.Add([pscustomobject]@{node=$numberRows.Count;parent=0;depth=1;type='Text';
                        name=$titleName;class_name=$info.ClassName;visible=$true;enabled=$info.IsEnabled})
                } catch { $numberErrors.Add($_.Exception.Message) }
            }
        } catch { $numberErrors.Add($_.Exception.Message) }
        return [ordered]@{ok=$true;read_only=$true;scope='profile';scope_runtime_id=$profileId;
            scope_class='class Info::Profile::Widget';process_id=$expectedPid;window_handle=$handleValue;
            profile_number_only=$true;visited=$numberRows.Count;truncated=$false;
            controls=@($numberRows.ToArray());errors=@($numberErrors.ToArray())}
    }
    # Focus on the active popup before enumerating, so chats cannot consume the cap.
    $scope = 'window'
    $inspectionRoot = $root
    $contacts = @(VisibleClass 'class AddContactBox')
    $boxes = @(VisibleClass 'class Ui::BoxLayerWidget')
    $queueProbe = $false
    $profileNumberOnly = $false
    if ($env:TG_ACTION_PAYLOAD) {
        $probePayload = $env:TG_ACTION_PAYLOAD | ConvertFrom-Json
        $queueProbe = ($probePayload.queue_probe -eq $true)
        $profileNumberOnly = ($probePayload.profile_number_only -eq $true)
    }
    if ($queueProbe -and $boxes.Count -gt 1) {
        # A restriction popup may overlay New Contact. Read only the focused
        # modal in this exact window; never guess a box by screen position.
        $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
        $focusedBox = $null
        $belongsToRoot = $false
        $rootId = $root.GetRuntimeId() -join ','
        for ($i = 0; $i -lt 64 -and $null -ne $focused; $i++) {
            if ($focused.Current.ProcessId -ne $expectedPid) { break }
            if (($focused.GetRuntimeId() -join ',') -eq $rootId) { $belongsToRoot=$true; break }
            if ($null -eq $focusedBox -and $focused.Current.ClassName -eq 'class Ui::BoxLayerWidget' -and (IsVisible $focused)) {
                $focusedBox = $focused
            }
            $focused = $walker.GetParent($focused)
        }
        if (-not $belongsToRoot -or $null -eq $focusedBox) {
            throw 'Multiple dialogs and no uniquely focused modal in the bound window. Stop and inspect manually.'
        }
        $focusedId = $focusedBox.GetRuntimeId() -join ','
        $matches = @($boxes | Where-Object { ($_.GetRuntimeId() -join ',') -eq $focusedId })
        if ($matches.Count -ne 1) { throw 'Focused modal identity is not unique.' }
        $boxes = @($focusedBox)
        $contacts = @($contacts | Where-Object {
            $p = $walker.GetParent($_)
            $null -ne $p -and ($p.GetRuntimeId() -join ',') -eq $focusedId
        })
    }
    if ($contacts.Count -gt 1 -or $boxes.Count -gt 1) {
        throw 'Multiple visible dialogs. Close extra dialogs first.'
    }
    if ($contacts.Count -eq 1) {
        $parent = $walker.GetParent($contacts[0])
        if ($null -eq $parent -or $parent.Current.ClassName -ne 'class Ui::BoxLayerWidget') {
            throw 'Contact dialog container is not recognized.'
        }
        $inspectionRoot = $parent
        $scope = 'contact_dialog'
    } elseif ($boxes.Count -eq 1) {
        # Peer selection dialogs overlay a profile which Qt still calls visible.
        # Inspect the modal box first; do not accidentally read that background profile.
        $inspectionRoot = $boxes[0]
        $scope = 'dialog'
    } else {
        $profiles = @(VisibleClass 'class Info::Profile::Widget')
        if ($profiles.Count -gt 1) { throw 'Multiple visible profiles. Close extra panels first.' }
        if ($profiles.Count -eq 1) {
            $inspectionRoot = $profiles[0]
            $scope = 'profile'
        }
    }
    if ($profileNumberOnly) {
        if ($scope -eq 'profile') {
            ReadProfileNumberReport $inspectionRoot | ConvertTo-Json -Depth 8 -Compress
        } else {
            # A profile may still be appearing after navigation. Report its
            # absence/overlaid modal without enumerating unrelated chat fields.
            [ordered]@{ok=$true;read_only=$true;scope=$scope;
                scope_runtime_id=($inspectionRoot.GetRuntimeId() -join ',');scope_class=$inspectionRoot.Current.ClassName;
                process_id=$expectedPid;window_handle=$handleValue;profile_number_only=$true;
                profile_number_unavailable=$true;visited=0;truncated=$false;controls=@();errors=@()} |
                ConvertTo-Json -Depth 8 -Compress
        }
        return
    }
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue(@{element=$inspectionRoot; depth=0; parent=-1})
    $rows = New-Object System.Collections.Generic.List[object]
    $errors = New-Object System.Collections.Generic.List[string]
    $visited = 0
    $limit = 400
    $depthLimit = 32
    $truncated = $false
    function FiniteOrNull([double]$value) {
        if ([double]::IsInfinity($value) -or [double]::IsNaN($value)) { return $null }
        return $value
    }
    while ($queue.Count -gt 0 -and $visited -lt $limit) {
        $entry = $queue.Dequeue()
        $element = $entry.element
        $depth = [int]$entry.depth
        $nodeId = $visited
        $visited++
        try {
            $info = $element.Current
            if ($info.ProcessId -ne $expectedPid) { continue }
            $type = $info.ControlType.ProgrammaticName.Replace('ControlType.', '')
            $name = $info.Name
            $supportedIds = @($element.GetSupportedPatterns() | ForEach-Object { $_.Id })
            $toggleState = $null
            $selectionSelected = $null
            $pattern = $null
            if ($element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) {
                $toggleState = $pattern.Current.ToggleState.ToString()
            }
            $pattern = $null
            if ($element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {
                $selectionSelected = $pattern.Current.IsSelected
            }
            $row = [ordered]@{
                node=$nodeId; parent=$entry.parent; depth=$depth;
                type=$type; name=$name; automation_id=$info.AutomationId;
                class_name=$info.ClassName; enabled=$info.IsEnabled;
                offscreen=$info.IsOffscreen; visible=(IsVisible $element);
                value_pattern=($supportedIds -contains ([System.Windows.Automation.ValuePattern]::Pattern).Id);
                invoke_pattern=($supportedIds -contains ([System.Windows.Automation.InvokePattern]::Pattern).Id);
                toggle_state=$toggleState; selection_selected=$selectionSelected;
                bounds=@{left=(FiniteOrNull $info.BoundingRectangle.Left); top=(FiniteOrNull $info.BoundingRectangle.Top); width=(FiniteOrNull $info.BoundingRectangle.Width); height=(FiniteOrNull $info.BoundingRectangle.Height)}
            }
            # Text/document/list names may contain chats. Keep topology but omit those names.
            if ($scope -in @('profile','dialog','contact_dialog') -and $type -eq 'Text') {
                # Read the inspected profile/dialog, never chat history or edit values.
                if ([string]::IsNullOrWhiteSpace($row.name)) {
                    $pattern = $null
                    if ($element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) {
                        $row.name = $pattern.DocumentRange.GetText(512)
                    }
                }
            } elseif ($scope -ne 'dialog' -and $type -notin @('Button','Edit','Window','MenuItem','ComboBox')) {
                $row.name = ''
            }
            $rows.Add([pscustomobject]$row)
            # Reaching the depth limit at a leaf does not omit any controls.
            $child = $walker.GetFirstChild($element)
            if ($depth -lt $depthLimit) {
                while ($null -ne $child) {
                    if (($queue.Count + $visited) -ge $limit) { $truncated=$true; break }
                    $queue.Enqueue(@{element=$child;depth=$depth+1;parent=$nodeId})
                    $child = $walker.GetNextSibling($child)
                }
            } elseif ($null -ne $child) { $truncated=$true }
        } catch {
            $errors.Add($_.Exception.Message)
        }
    }
    if ($queue.Count -gt 0) { $truncated=$true }
    [ordered]@{ok=$true;read_only=$true;scope=$scope;scope_runtime_id=($inspectionRoot.GetRuntimeId() -join ',');scope_class=$inspectionRoot.Current.ClassName;process_id=$expectedPid;window_handle=$handleValue;visited=$visited;truncated=$truncated;controls=@($rows.ToArray());errors=@($errors.ToArray())} | ConvertTo-Json -Depth 8 -Compress
} catch {
    [ordered]@{ok=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress
    return
}
