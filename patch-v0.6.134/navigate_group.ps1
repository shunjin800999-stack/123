# Navigation only: open an exact live chat row, Info, then Add members.
# Never invokes the final Add/Invite button, types messages or closes a dialog.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$report=[ordered]@{ok=$false;scope='pinned_group_navigation';read_only=$false;
    state='review';final_invite_clicked=$false;members_selected=$false;
    contact_database_updated=$false;actions=@();errors=@();stage='initial'}
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $payload=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
    $handleValue=[Int64]::Parse($env:TG_INSPECT_HWND)
    $expectedPid=[Int32]::Parse($env:TG_INSPECT_PID)
    $report.window_handle=$handleValue;$report.process_id=$expectedPid
    $report.slot=[int]$payload.slot
    if ($report.slot -notin @(1,2)) { throw 'Only target group 1 or 2 is supported.' }
    if (@($payload.targets).Count -ne 2) { throw 'Exactly two previously observed targets are required.' }
    $target=@($payload.targets)[$report.slot-1]
    $report.group_name=[string]$target.name
    $report.group_runtime_id=[string]$target.runtime_id
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    if ($null -eq $root -or $root.Current.ProcessId -ne $expectedPid -or $root.Current.ClassName -ne 'class MainWindow') {
        throw 'Telegram window identity changed. Rescan and record the pinned groups again.'
    }
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
        $items=@($elements)
        if ($items.Count -ne 1) { throw ('Expected one '+$description+', found '+$items.Count+'.') }
        return $items[0]
    }
    function AssertRoot {
        $current=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
        if ($null -eq $current -or $current.Current.ProcessId -ne $expectedPid -or $current.Current.ClassName -ne 'class MainWindow') {
            throw 'Window identity changed during navigation.'
        }
    }
    function AssertNoModal {
        AssertRoot
        if (@(FindClass $root 'class Ui::BoxLayerWidget').Count -gt 0) {
            throw 'An existing dialog is open. Finish or close it manually first; its selections were preserved.'
        }
    }
    function Button($parent,$names,$classes,[bool]$requireEnabled=$true) {
        $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)
        $all=@($parent.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object {
            (Visible $_) -and (-not $requireEnabled -or $_.Current.IsEnabled) -and $_.Current.Name -in $names -and $_.Current.ClassName -in $classes
        })
        if ($all.Count -ne 1) {
            $report.observed_navigation_buttons=@($parent.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object {
                (Visible $_) -and $_.Current.ClassName -in $classes
            } | ForEach-Object { [pscustomobject]@{name=$_.Current.Name;class_name=$_.Current.ClassName;enabled=$_.Current.IsEnabled} })
        }
        return (Single $all ('navigation button: '+($names -join '/')))
    }
    function InvokeNavigation($element,[string]$action) {
        AssertRoot
        $c=$element.Current
        if (-not (Visible $element) -or -not $c.IsEnabled) { throw 'Navigation control is unavailable.' }
        # A closed action vocabulary: final Add, Invite, Send and other actions
        # cannot reach InvokePattern through this function.
        if ($action -eq 'open_chat') {
            if ($c.ControlType -ne [System.Windows.Automation.ControlType]::ListItem -or
                ($element.GetRuntimeId() -join ',') -ne [string]$target.runtime_id) { throw 'Chat row changed.' }
        } elseif ($action -eq 'open_info') {
            if ($c.ControlType -ne [System.Windows.Automation.ControlType]::Button -or
                $c.Name -notin @('Info','Informações','Informação','信息','资料')) { throw 'Unsupported Info control.' }
        } elseif ($action -eq 'open_members') {
            if ($c.ControlType -ne [System.Windows.Automation.ControlType]::Button -or
                $c.Name -notin @('Add members','Add Members','Adicionar membros','Adicionar Membros','Adicionar participantes','Adicionar Participantes','添加成员')) { throw 'Unsupported Add members control.' }
        } else { throw 'Unknown navigation action.' }
        $pattern=$null
        if (-not $element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) {
            $report.supported_pattern_ids=@($element.GetSupportedPatterns() | ForEach-Object { $_.Id })
            throw ('No InvokePattern for '+$action+'. No keyboard or coordinate fallback was attempted.')
        }
        $entry=[pscustomobject]@{action=$action;runtime_id=($element.GetRuntimeId() -join ',');attempted=$true;invoked=$false}
        $report.actions+=@($entry)
        ([System.Windows.Automation.InvokePattern]$pattern).Invoke()
        $entry.invoked=$true
    }
    AssertNoModal
    if (@(FindClass $root 'class Info::Profile::Widget').Count -gt 0) {
        throw 'Return to the ordinary chat page and close the profile panel before this test.'
    }
    $report.stage='live_pinned_guard'
    $list=Single @(FindClass $root 'class Dialogs::InnerWidget') 'chat list'
    if (($list.GetRuntimeId() -join ',') -ne [string]$payload.list_runtime_id) {
        throw 'Chat list instance changed. Record the pinned groups again.'
    }
    $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem)
    $rows=@($list.FindAll([System.Windows.Automation.TreeScope]::Children,$condition))
    if ($rows.Count -lt 2) { throw 'The first two chat rows are unavailable.' }
    for ($i=0;$i -lt 2;$i++) {
        $expected=@($payload.targets)[$i];$row=$rows[$i];$c=$row.Current
        if (($row.GetRuntimeId() -join ',') -ne [string]$expected.runtime_id -or $c.ProcessId -ne $expectedPid) {
            throw 'Pinned row identity or order changed. Record the pinned groups again.'
        }
        $prefix=[string]$expected.group_label+', '+[string]$expected.name+', '
        if (-not ([string]$c.Name).StartsWith($prefix,[StringComparison]::Ordinal)) { throw 'Pinned group name changed.' }
        $tail=([string]$c.Name).Substring($prefix.Length)
        $pin=[string]$expected.pinned_label
        $muted=if ([string]$expected.language -eq 'pt') { 'Silenciado' } else { 'Muted' }
        if ($tail.StartsWith($muted+', ',[StringComparison]::Ordinal)) { $tail=$tail.Substring($muted.Length+2) }
        if ($tail -cne $pin -and -not $tail.StartsWith($pin+', ',[StringComparison]::Ordinal)) { throw 'The group is no longer pinned.' }
        $fields=New-Object System.Collections.Generic.List[string]
        $field=$walker.GetFirstChild($row);$n=0
        while ($null -ne $field) {
            $n++;if ($n -gt 32) { throw 'Too many chat row fields.' }
            if ($field.Current.ControlType -eq [System.Windows.Automation.ControlType]::DataItem) { $fields.Add([string]$field.Current.Name) }
            $field=$walker.GetNextSibling($field)
        }
        if ($pin -notin @($fields.ToArray())) { throw 'Independent pinned field is missing.' }
    }
    $row=$rows[$report.slot-1]
    $report.stage='open_chat'
    InvokeNavigation $row 'open_chat'
    Start-Sleep -Milliseconds 500
    AssertNoModal
    # If titles within this window are identical, require explicit selection
    # evidence; a name alone cannot prove which same-named row was activated.
    $selected=$null;$selection=$null
    if ($row.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$selection)) {
        $selected=([System.Windows.Automation.SelectionItemPattern]$selection).Current.IsSelected
    } else {
        $legacy=$null
        if ($row.TryGetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern,[ref]$legacy)) {
            $selected=((([System.Windows.Automation.LegacyIAccessiblePattern]$legacy).Current.State -band 2) -ne 0)
        }
    }
    $report.row_selected=$selected
    $sameName=@($rows | Where-Object { ([string]$_.Current.Name).StartsWith([string]$target.group_label+', '+[string]$target.name+', ',[StringComparison]::Ordinal) })
    if ($selected -eq $false -or ($sameName.Count -gt 1 -and $selected -ne $true)) {
        throw 'Could not verify activation of the exact row; same-named chats require explicit selection evidence.'
    }
    $bar=Single @(FindClass $root 'class HistoryView::TopBarWidget') 'chat top bar'
    $report.stage='open_info'
    $info=Button $bar @('Info','Informações','Informação','信息','资料') @('class Ui::IconButton')
    InvokeNavigation $info 'open_info'
    $profile=$null
    for ($i=0;$i -lt 10;$i++) {
        Start-Sleep -Milliseconds 200
        $profiles=@(FindClass $root 'class Info::Profile::Widget')
        if ($profiles.Count -eq 1) { $profile=$profiles[0];break }
        if ($profiles.Count -gt 1) { throw 'Multiple group profiles are visible.' }
    }
    if ($null -eq $profile) { throw 'Group Info did not open.' }
    $profileId=$profile.GetRuntimeId() -join ','
    $report.stage='verify_profile'
    function VerifyProfile {
        AssertNoModal
        $current=Single @(FindClass $root 'class Info::Profile::Widget') 'group profile'
        if (($current.GetRuntimeId() -join ',') -ne $profileId) { throw 'Group profile instance changed.' }
        $names=@(FindClass $current 'class Ui::MarqueeLabel' | Where-Object {
            $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and $_.Current.Name -ceq [string]$target.name
        })
        if ($names.Count -ne 1) { throw 'The opened group name does not exactly match the target.' }
        $sections=@(FindClass $current 'class Info::Profile::Members')
        if ($sections.Count -eq 0) { return $null }
        $members=Single $sections 'group member section'
        $buttons=@(foreach ($class in @('class Ui::IconButton','class Ui::SettingsButton')) {
            FindClass $members $class | Where-Object {
                $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
                $_.Current.IsEnabled -and $_.Current.Name -in @('Add members','Add Members','Adicionar membros','Adicionar Membros','Adicionar participantes','Adicionar Participantes','添加成员')
            }
        })
        if ($buttons.Count -eq 0) { return $null }
        return (Single $buttons 'Add members button')
    }
    function WaitMemberButton {
        $clock=[System.Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            $button=VerifyProfile
            if ($null -ne $button) { return $button }
            if ($clock.ElapsedMilliseconds -ge 2500) {
                throw 'Verified group profile has no visible enabled Add members entry after 2.5s; check group access or page layout.'
            }
            Start-Sleep -Milliseconds 50
        }
    }
    $memberWait=[System.Diagnostics.Stopwatch]::StartNew()
    $memberButton=WaitMemberButton
    $report.member_ready_wait_ms=$memberWait.ElapsedMilliseconds
    $memberId=$memberButton.GetRuntimeId() -join ','
    Start-Sleep -Milliseconds 300
    $memberButton=WaitMemberButton
    if (($memberButton.GetRuntimeId() -join ',') -ne $memberId) { throw 'Add members navigation button changed.' }
    $report.profile_verified=$true;$report.profile_runtime_id=$profileId
    $report.stage='open_members'
    InvokeNavigation $memberButton 'open_members'
    $box=$null
    for ($i=0;$i -lt 10;$i++) {
        Start-Sleep -Milliseconds 200
        $boxes=@(FindClass $root 'class Ui::BoxLayerWidget')
        if ($boxes.Count -eq 1) { $box=$boxes[0];break }
        if ($boxes.Count -gt 1) { throw 'Multiple dialogs opened; check manually.' }
    }
    if ($null -eq $box) { throw 'Add members dialog did not open.' }
    $report.stage='verify_members_dialog'
    $peer=Single @(FindClass $box 'class PeerListBox') 'member selector'
    $titles=@(FindClass $box 'class Ui::FlatLabel' | Where-Object {
        $_.Current.Name -in @('Add Members','Add members','Adicionar Membros','Adicionar membros','Adicionar Participantes','Adicionar participantes','添加成员')
    })
    if ($titles.Count -ne 1) { throw 'The opened dialog is not an identified Add members dialog.' }
    # Merely read final buttons to verify the expected dialog. Never invoke them.
    $add=Button $box @('Add','Adicionar','添加') @('class Ui::RoundButton') $false
    $cancel=Button $box @('Cancel','Cancelar','取消') @('class Ui::RoundButton')
    $report.dialog_runtime_id=$box.GetRuntimeId() -join ','
    $report.dialog_title=$titles[0].Current.Name
    $report.ok=$true;$report.state='members_open';$report.stage='done'
    $report.reason='The verified target group is open at Add members. No contacts were selected and the final Add was not invoked.'
    $report | ConvertTo-Json -Depth 10 -Compress
} catch {
    $report.error=$_.Exception.Message
    $report | ConvertTo-Json -Depth 10 -Compress
    return
}
