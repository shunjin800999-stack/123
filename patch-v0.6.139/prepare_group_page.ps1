# Known Cancel/Close panel/main search only. Never Create, Send, final Add or Esc.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$report=[ordered]@{ok=$false;scope='group_page_cleanup';state='review';stage='initial';actions=@();errors=@();
    members_selected=$false;create_attempted=$false;final_invite_clicked=$false;contact_database_updated=$false;invite_result_inferred=$false;
    modal_absent=$false;profile_absent=$false;search_empty=$false;first_two_visible=$false}
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName WindowsBase
    if (-not ('ChatListWheel' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ChatListWheel {
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X,Y; }
    [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT {
        public int dx,dy; public uint mouseData,dwFlags,time; public UIntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Explicit)] struct UNION { [FieldOffset(0)] public MOUSEINPUT mi; }
    [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public UNION data; }
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(POINT point);
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr hwnd,uint flag);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll",SetLastError=true)] static extern uint SendInput(uint count,INPUT[] input,int size);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
    public static void SetDpi() { try { SetThreadDpiAwarenessContext(new IntPtr(-4)); } catch(EntryPointNotFoundException) {} }
    static void Identity(IntPtr hwnd,int expectedPid) {
        uint pid;
        if(!IsWindow(hwnd)||IsIconic(hwnd)||GetWindowThreadProcessId(hwnd,out pid)==0||pid!=(uint)expectedPid)
            throw new Exception("Bound window is changed or minimized; no wheel.");
    }
    static void IdleKeys() {
        foreach(int key in new int[]{1,2,4,16,17,18})
            if((GetAsyncKeyState(key)&0x8000)!=0) throw new Exception("Mouse button or modifier is held; no wheel.");
    }
    public static void Activate(IntPtr hwnd,int pid) {
        Identity(hwnd,pid);IdleKeys();
        if(GetForegroundWindow()!=hwnd) {
            SetForegroundWindow(hwnd);
            for(int i=0;i<10&&GetForegroundWindow()!=hwnd;i++) System.Threading.Thread.Sleep(50);
        }
        if(GetForegroundWindow()!=hwnd) throw new Exception("Cannot activate the bound Telegram window; no wheel.");
    }
    public static void Position(IntPtr hwnd,int pid,int x,int y) {
        Identity(hwnd,pid);IdleKeys();
        if(GetForegroundWindow()!=hwnd) throw new Exception("Foreground changed; no wheel.");
        POINT p=new POINT();p.X=x;p.Y=y;
        if(GetAncestor(WindowFromPoint(p),2)!=hwnd) throw new Exception("List point is covered by another window; no wheel.");
        if(!SetCursorPos(x,y)) throw new Exception("Cannot position pointer inside verified chat list.");
    }
    public static void WheelUp(IntPtr hwnd,int pid,int x,int y) {
        Identity(hwnd,pid);IdleKeys();POINT p;
        if(GetForegroundWindow()!=hwnd||!GetCursorPos(out p)||p.X!=x||p.Y!=y||GetAncestor(WindowFromPoint(p),2)!=hwnd)
            throw new Exception("Pointer or foreground changed; no wheel.");
        INPUT[] input=new INPUT[1];input[0].data.mi.dwFlags=0x0800;input[0].data.mi.mouseData=1200;
        if(SendInput(1,input,Marshal.SizeOf(typeof(INPUT)))!=1)
            throw new Exception("Wheel insertion uncertain; no automatic retry.");
    }
}
'@
    }
    [ChatListWheel]::SetDpi()
    $payload=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
    $handleValue=[Int64]::Parse($env:TG_INSPECT_HWND);$expectedPid=[Int32]::Parse($env:TG_INSPECT_PID)
    $scanPrepare=($payload.scan_prepare -eq $true);$report.scan_prepare=$scanPrepare
    $contactOnly=($payload.contact_only -eq $true);$report.contact_only=$contactOnly
    if ([string]::IsNullOrWhiteSpace([string]$payload.executable_path) -or (-not $contactOnly -and ([string]::IsNullOrWhiteSpace([string]$payload.list_runtime_id) -or @($payload.target_names).Count -ne 2))) { throw 'Missing frozen window and target binding.' }
    $expectedPath=[IO.Path]::GetFullPath([string]$payload.executable_path)
    $targetNames=@($payload.target_names | ForEach-Object { [string]$_ })
    if (-not $contactOnly -and @($payload.targets).Count -ne 2) { throw 'Missing first two frozen target rows.' }
    if ($targetNames | Where-Object { [string]::IsNullOrWhiteSpace($_) }) { throw 'Missing group name.' }
    $contacts=@($payload.contacts)
    foreach ($contact in $contacts) {
        if ([string]$contact.number -notmatch '^(0|[1-9][0-9]{0,5})$' -or [string]$contact.phone -notmatch '^\+[1-9][0-9]{6,14}$') { throw 'Invalid saved contact snapshot.' }
    }
    if (@($contacts | Group-Object number | Where-Object { $_.Count -ne 1 }).Count -gt 0) { throw 'Ambiguous saved contact numbers.' }
    $report.window_handle=$handleValue;$report.process_id=$expectedPid;$report.executable_path=$expectedPath;$report.target_names=$targetNames
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    if ($null -eq $root -or $root.Current.ProcessId -ne $expectedPid -or $root.Current.ClassName -ne 'class MainWindow') { throw 'Bound Telegram window changed.' }
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
            ($now.GetRuntimeId() -join ',') -ne $rootId -or -not (Visible $now)) { throw 'Window changed during page preparation.' }
        $process=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = '+$expectedPid)
        if ($null -eq $process -or [string]::IsNullOrEmpty($process.ExecutablePath) -or
            -not [string]::Equals([IO.Path]::GetFullPath($process.ExecutablePath),$expectedPath,[StringComparison]::OrdinalIgnoreCase)) { throw 'Bound executable changed.' }
        $report.process_path_verified=$true
    }
    function NoModal {
        AssertRoot
        if (@(FindClass $root 'class Ui::BoxLayerWidget').Count -ne 0) { throw 'Unknown or newly appeared modal was preserved.' }
    }
    function DescribeProfile {
        AssertRoot
        $profile=Single @(FindClass $root 'class Info::Profile::Widget') 'profile'
        $names=@(FindClass $profile 'class Ui::MarqueeLabel' | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text })
        $nameElement=Single $names 'profile name';$name=[string]$nameElement.Current.Name
        if ($scanPrepare) {
            $report.profile_runtime_id=$profile.GetRuntimeId() -join ',';$report.profile_kind='scan_panel'
            return [pscustomobject]@{element=$profile;runtime_id=$report.profile_runtime_id;kind='scan_panel';name=$name}
        }
        $members=@(FindClass $profile 'class Info::Profile::Members')
        $kind='';$phoneVerified=$false;$savedVerified=$false
        if ($members.Count -eq 1 -and ($contactOnly -or @($targetNames | Where-Object { $_ -ceq $name }).Count -gt 0)) {
            $kind='group';$report.group_members_verified=$true
        } elseif ($members.Count -eq 0) {
            $saved=Single @($contacts | Where-Object { [string]$_.number -ceq $name }) 'saved numeric contact'
            $all=@($profile.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object { Visible $_ })
            $phones=@($all | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and
                [regex]::Replace([string]$_.Current.Name,'[\s()\-]','') -ceq [string]$saved.phone })
            if ($phones.Count -ne 1) { throw 'Saved contact phone differs; profile preserved.' }
            foreach ($labels in @(@('Edit contact','Editar contato','编辑联系人'),@('Delete contact','Apagar contato','删除联系人'))) {
                $markers=@($all | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name -in $labels })
                if ($markers.Count -ne 1) { throw 'Saved contact Edit/Delete markers unavailable.' }
            }
            $kind='contact';$phoneVerified=$true;$savedVerified=$true
            $sha=[System.Security.Cryptography.SHA256]::Create()
            try { $report.phone_sha256=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$saved.phone)))).Replace('-','').ToLowerInvariant() }
            finally { $sha.Dispose() }
        } else { throw 'Profile is not a saved contact or either frozen target group; preserved.' }
        $report.profile_kind=$kind;$report.profile_name=$name;$report.profile_runtime_id=$profile.GetRuntimeId() -join ','
        $report.profile_verified=$true;$report.phone_verified=$phoneVerified;$report.saved_contact_verified=$savedVerified
        return [pscustomobject]@{element=$profile;runtime_id=$report.profile_runtime_id;kind=$kind;name=$name}
    }
    function Button($parent,$labels,[string]$class) {
        $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)
        return (Single @($parent.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object {
            (Visible $_) -and $_.Current.IsEnabled -and $_.Current.Name -in $labels -and $_.Current.ClassName -ceq $class
        }) 'allowed cleanup button')
    }
    function InvokeCleanup($button,[string]$action,[string]$expectedId) {
        AssertRoot
        if (($button.GetRuntimeId() -join ',') -ne $expectedId -or -not (Visible $button) -or -not $button.Current.IsEnabled) { throw 'Cleanup control changed.' }
        $c=$button.Current
        if ($action -eq 'cancel_member_selector') {
            if ($c.Name -notin @('Cancel','Cancelar','取消','Close','Fechar','关闭') -or $c.ClassName -ne 'class Ui::RoundButton') { throw 'Only known selector Cancel is allowed.' }
        } elseif ($action -eq 'close_profile') {
            if ($c.Name -notin @('Close panel','Fechar painel') -or $c.ClassName -cne 'class Info::Profile::`anonymous namespace''::BackdropIconButton') { throw 'Only known profile Close panel is allowed.' }
        } else { throw 'Unsupported cleanup button action.' }
        $pattern=$null
        if (-not $button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) { throw 'No InvokePattern; no input fallback.' }
        $actionReport=[pscustomobject]@{action=$action;runtime_id=$expectedId;attempted=$true;invoked=$false}
        $report.actions+=@($actionReport)
        ([System.Windows.Automation.InvokePattern]$pattern).Invoke();$actionReport.invoked=$true
    }
    function CancelButton {
        if ($scanPrepare) {
            $box=Single @(FindClass $root 'class Ui::BoxLayerWidget') 'scan modal'
            $report.dialog_runtime_id=$box.GetRuntimeId() -join ','
            return (Button $box @('Cancel','Cancelar','取消','Close','Fechar','关闭') 'class Ui::RoundButton')
        }
        $profile=DescribeProfile
        if ($profile.kind -cne 'group') { throw 'A member selector requires a known target group profile.' }
        $box=Single @(FindClass $root 'class Ui::BoxLayerWidget') 'known member modal'
        $peer=Single @(FindClass $box 'class PeerListBox') 'member selector'
        $title=Single @(FindClass $box 'class Ui::FlatLabel' | Where-Object { $_.Current.Name -in @('Add Members','Add members','Adicionar Membros','Adicionar membros','Adicionar Participantes','Adicionar participantes','添加成员') }) 'member dialog title'
        $report.dialog_runtime_id=$box.GetRuntimeId() -join ',';$report.member_dialog_verified=$true
        return (Button $box @('Cancel','Cancelar','取消') 'class Ui::RoundButton')
    }
    AssertRoot
    $boxes=@(FindClass $root 'class Ui::BoxLayerWidget')
    if ($boxes.Count -gt 1) { throw 'Multiple dialogs preserved.' }
    if ($boxes.Count -eq 1) {
        $report.stage='cancel_known_selector';$cancel=CancelButton;$cancelId=$cancel.GetRuntimeId() -join ','
        $profileId=$report.profile_runtime_id;$boxId=$report.dialog_runtime_id
        Start-Sleep -Milliseconds 300;$cancel=CancelButton
        if ($report.profile_runtime_id -ne $profileId -or $report.dialog_runtime_id -ne $boxId) { throw 'Selector or underlying profile changed.' }
        InvokeCleanup $cancel 'cancel_member_selector' $cancelId
        for ($i=0;$i -lt 20;$i++) { Start-Sleep -Milliseconds 100;AssertRoot;if (@(FindClass $root 'class Ui::BoxLayerWidget').Count -eq 0) { break } }
        NoModal
    }
    NoModal
    $profiles=@(FindClass $root 'class Info::Profile::Widget')
    if ($profiles.Count -gt 1) { throw 'Multiple profiles preserved.' }
    if ($profiles.Count -eq 1) {
        $report.stage='close_known_profile';$profile=DescribeProfile;$profileId=$profile.runtime_id
        $bar=Single @(FindClass $profile.element 'class Info::Profile::TopBar') 'profile top bar'
        $close=Button $bar @('Close panel','Fechar painel') 'class Info::Profile::`anonymous namespace''::BackdropIconButton'
        $closeId=$close.GetRuntimeId() -join ','
        Start-Sleep -Milliseconds 300;NoModal;$profile=DescribeProfile
        if ($profile.runtime_id -ne $profileId) { throw 'Profile instance changed.' }
        $bar=Single @(FindClass $profile.element 'class Info::Profile::TopBar') 'profile top bar'
        $close=Button $bar @('Close panel','Fechar painel') 'class Info::Profile::`anonymous namespace''::BackdropIconButton'
        InvokeCleanup $close 'close_profile' $closeId
        for ($i=0;$i -lt 20;$i++) { Start-Sleep -Milliseconds 100;NoModal;if (@(FindClass $root 'class Info::Profile::Widget').Count -eq 0) { break } }
        if (@(FindClass $root 'class Info::Profile::Widget').Count -ne 0) { throw 'Profile closure not verified; no retry.' }
    }
    NoModal;$report.modal_absent=$true;$report.profile_absent=$true
    function ChatSearch {
        NoModal
        if (@(FindClass $root 'class Info::Profile::Widget').Count -ne 0) { throw 'Profile reappeared.' }
        $dialogs=Single @(FindClass $root 'class Dialogs::Widget') 'normal chat-list panel'
        $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit)
        $wrapper=Single @($dialogs.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object {
            (Visible $_) -and $_.Current.IsEnabled -and $_.Current.ClassName -ceq 'class Ui::InputField' -and $_.Current.Name -in @('Search','Pesquisar','Buscar','搜索')
        }) 'normal chat search wrapper'
        $inner=Single @(FindClass $wrapper 'class Ui::InputField::Inner' | Where-Object {
            $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and $_.Current.IsEnabled -and
            $_.Current.Name -in @('Search','Pesquisar','Buscar','搜索')
        }) 'actual normal chat search editor'
        $wrapperId=$wrapper.GetRuntimeId() -join ','
        if ($report.search_wrapper_runtime_id -and $report.search_wrapper_runtime_id -cne $wrapperId) { throw 'Search wrapper changed.' }
        $report.search_wrapper_runtime_id=$wrapperId;$report.search_class=$inner.Current.ClassName
        return $inner
    }
    function ReadSearch($search,[string]$stage) {
        if (($search.GetRuntimeId() -join ',') -cne $searchId -or $search.Current.ClassName -cne 'class Ui::InputField::Inner') { throw 'Actual search editor changed.' }
        $valuePattern=$null
        if (-not $search.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$valuePattern)) { throw 'Actual search editor ValuePattern unavailable.' }
        $valueLength=([string]([System.Windows.Automation.ValuePattern]$valuePattern).Current.Value).Length
        $textPattern=$null;$textLength=$null
        $textSupported=$search.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$textPattern)
        if ($textSupported) {
            $textLength=(([System.Windows.Automation.TextPattern]$textPattern).DocumentRange.GetText(-1).TrimEnd([char[]]@(13,10))).Length
        }
        $report.search_reads+=@([pscustomobject]@{stage=$stage;runtime_id=$searchId;class_name=$search.Current.ClassName;
            value_length=$valueLength;text_supported=$textSupported;text_length=$textLength})
        return ($valueLength -eq 0 -and (-not $textSupported -or $textLength -eq 0))
    }
    $report.stage='clear_main_search';$search=ChatSearch;$searchId=$search.GetRuntimeId() -join ',';$report.search_runtime_id=$searchId
    $report.search_reads=@();$wasEmpty=ReadSearch $search 'initial'
    if (-not $wasEmpty) {
        Start-Sleep -Milliseconds 300;$search=ChatSearch
        if (($search.GetRuntimeId() -join ',') -ne $searchId) { throw 'Search control changed.' }
        $value=$null
        if (-not $search.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value) -or ([System.Windows.Automation.ValuePattern]$value).Current.IsReadOnly) { throw 'Search cannot be cleared safely.' }
        $actionReport=[pscustomobject]@{action='clear_chat_search';runtime_id=$searchId;attempted=$true;invoked=$false}
        $report.actions+=@($actionReport)
        ([System.Windows.Automation.ValuePattern]$value).SetValue('');$actionReport.invoked=$true
        Start-Sleep -Milliseconds 400
    }
    $search=ChatSearch
    if (($search.GetRuntimeId() -join ',') -ne $searchId) { throw 'Search instance changed after clearing.' }
    if (-not (ReadSearch $search 'before_scroll')) { throw 'Actual main search is not empty.' }
    $report.search_empty=$true
    function ChatList {
        NoModal
        $list=Single @(FindClass $root 'class Dialogs::InnerWidget') 'normal chat list'
        if (($list.GetRuntimeId() -join ',') -ne [string]$payload.list_runtime_id) { throw 'Frozen chat list instance changed.' }
        return $list
    }
    function BoundRows($list) {
        $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem)
        $rows=@($list.FindAll([System.Windows.Automation.TreeScope]::Children,$condition))
        if ($rows.Count -lt 2) { throw 'First two saved target rows unavailable.' }
        $walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
        for ($i=0;$i -lt 2;$i++) {
            $expected=@($payload.targets)[$i];$row=$rows[$i];$c=$row.Current
            if ([int]$expected.slot -ne ($i+1) -or [string]$expected.name -cne $targetNames[$i] -or
                $c.ProcessId -ne $expectedPid -or ($row.GetRuntimeId() -join ',') -cne [string]$expected.runtime_id) { throw 'Pinned target row identity or order changed; no scrolling.' }
            $prefix=[string]$expected.group_label+', '+[string]$expected.name+', '
            if (-not ([string]$c.Name).StartsWith($prefix,[StringComparison]::Ordinal)) { throw 'Pinned target row name changed; no scrolling.' }
            $tail=([string]$c.Name).Substring($prefix.Length)
            $muted=if ([string]$expected.language -eq 'pt') { 'Silenciado' } else { 'Muted' }
            if ($tail.StartsWith($muted+', ',[StringComparison]::Ordinal)) { $tail=$tail.Substring($muted.Length+2) }
            $pin=[string]$expected.pinned_label
            if ($tail -cne $pin -and -not $tail.StartsWith($pin+', ',[StringComparison]::Ordinal)) { throw 'Target is no longer pinned; no scrolling.' }
            $fields=@();$field=$walker.GetFirstChild($row);$n=0
            while ($null -ne $field) {
                $n++;if ($n -gt 32 -or $field.Current.ProcessId -ne $expectedPid) { throw 'Pinned row fields changed.' }
                if ($field.Current.ControlType -eq [System.Windows.Automation.ControlType]::DataItem) { $fields+=@([string]$field.Current.Name) }
                $field=$walker.GetNextSibling($field)
            }
            if ($pin -notin $fields) { throw 'Independent pinned marker missing; no scrolling.' }
        }
        $report.first_two_bound=$true;$report.first_two_runtime_ids=@(($rows[0].GetRuntimeId() -join ','),($rows[1].GetRuntimeId() -join ','))
        return @($rows[0],$rows[1])
    }
    function FirstTwoVisible($list) {
        $rows=@(BoundRows $list);$area=ListViewport $list;$proofs=@();$allVisible=$true
        foreach ($row in $rows) {
            $b=$row.Current.BoundingRectangle
            $finite=$true
            foreach ($n in @($b.Left,$b.Top,$b.Width,$b.Height)) {
                if ([double]::IsNaN($n) -or [double]::IsInfinity($n)) { $finite=$false }
            }
            $inside=($finite -and (Visible $row) -and $b.Width -gt 0 -and $b.Height -gt 0 -and
                $b.Top -ge $area.top-1 -and $b.Bottom -le $area.top+$area.height+1 -and
                $b.Left+$b.Width/2 -gt $area.left -and $b.Left+$b.Width/2 -lt $area.left+$area.width)
            $proofs+=@([pscustomobject]@{runtime_id=($row.GetRuntimeId() -join ',');offscreen=$row.Current.IsOffscreen;
                bounds=[pscustomobject]@{left=$b.Left;top=$b.Top;width=$b.Width;height=$b.Height};within_viewport=$inside})
            if (-not $inside) { $allVisible=$false }
        }
        $report.target_visibility=[pscustomobject]@{viewport=$area;rows=$proofs}
        return $allVisible
    }
    function ListViewport($list) {
        $walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
        $area=$list.Current.BoundingRectangle;$left=$area.Left;$top=$area.Top;$right=$area.Right;$bottom=$area.Bottom
        $parent=$walker.GetParent($list);$reachedRoot=$false
        for ($i=0;$i -lt 16 -and $null -ne $parent;$i++) {
            if ($parent.Current.ProcessId -ne $expectedPid) { throw 'Viewport ancestor belongs to another window.' }
            $b=$parent.Current.BoundingRectangle
            if ($b.Width -gt 0 -and $b.Height -gt 0) {
                $left=[Math]::Max($left,$b.Left);$top=[Math]::Max($top,$b.Top)
                $right=[Math]::Min($right,$b.Right);$bottom=[Math]::Min($bottom,$b.Bottom)
            }
            if (($parent.GetRuntimeId() -join ',') -ceq $rootId) { $reachedRoot=$true;break }
            $parent=$walker.GetParent($parent)
        }
        foreach ($n in @($left,$top,$right,$bottom)) { if ([double]::IsNaN($n) -or [double]::IsInfinity($n)) { throw 'Invalid live viewport bounds.' } }
        if (-not $reachedRoot -or $right-$left -lt 40 -or $bottom-$top -lt 40) { throw 'No visible chat-list viewport for wheel input.' }
        return [pscustomobject]@{left=$left;top=$top;width=($right-$left);height=($bottom-$top)}
    }
    function GuardWheelPoint($list,[int]$x,[int]$y) {
        NoModal
        if (@(FindClass $root 'class Info::Profile::Widget').Count -ne 0) { throw 'A profile reappeared; no wheel.' }
        $searchNow=ChatSearch
        if (-not (ReadSearch $searchNow 'wheel_guard')) { throw 'Search changed; no wheel.' }
        $rows=@(BoundRows $list);$area=ListViewport $list
        if ($x -le $area.left+4 -or $x -ge $area.left+$area.width-4 -or $y -le $area.top+4 -or $y -ge $area.top+$area.height-4) { throw 'Wheel point left the live viewport.' }
        $under=[System.Windows.Automation.AutomationElement]::FromPoint([System.Windows.Point]::new($x,$y))
        $walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker;$bound=$false
        for ($i=0;$i -lt 20 -and $null -ne $under;$i++) {
            if ($under.Current.ProcessId -ne $expectedPid) { break }
            if (($under.GetRuntimeId() -join ',') -ceq [string]$payload.list_runtime_id) { $bound=$true;break }
            $under=$walker.GetParent($under)
        }
        if (-not $bound) { throw 'The live point is not inside the bound chat-list UI tree; no wheel.' }
        return $area
    }
    if ($contactOnly) {
        NoModal;$search=ChatSearch
        if (($search.GetRuntimeId() -join ',') -ne $searchId -or -not (ReadSearch $search 'final')) { throw 'Ordinary contact page changed.' }
        AssertRoot
        $report.contact_only_verified=$true;$report.ok=$true;$report.state='ready';$report.stage='verified'
        $report | ConvertTo-Json -Depth 8 -Compress
        return
    }
    $report.stage='verify_chat_list';$list=ChatList;$report.list_runtime_id=$list.GetRuntimeId() -join ',';$report.scroll_method='none'
    $report.first_rows=@(BoundRows $list | ForEach-Object { [pscustomobject]@{runtime_id=($_.GetRuntimeId() -join ',');offscreen=$_.Current.IsOffscreen;pattern_ids=@($_.GetSupportedPatterns() | ForEach-Object { $_.Id })} })
    $initialVisible=FirstTwoVisible $list;$report.initial_target_visibility=$report.target_visibility
    if (-not $initialVisible) {
        $scrollElement=$list;$scroll=$null;$walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
        for ($i=0;$i -lt 5;$i++) {
            if ($null -eq $scrollElement -or $scrollElement.Current.ProcessId -ne $expectedPid -or $scrollElement.Current.ClassName -eq 'class MainWindow') { break }
            if ($scrollElement.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern,[ref]$scroll) -and ([System.Windows.Automation.ScrollPattern]$scroll).Current.VerticallyScrollable) { break }
            $scroll=$null;$scrollElement=$walker.GetParent($scrollElement)
        }
        $rows=@(BoundRows $list);$itemScroll=$null
        if ($null -ne $scroll) {
            $report.scroll_method='ScrollPattern'
            AssertRoot;$actionReport=[pscustomobject]@{action='scroll_chat_list';runtime_id=($scrollElement.GetRuntimeId() -join ',');attempted=$true;invoked=$false}
            $report.actions+=@($actionReport);$report.scroll_binding_verified=$true
            ([System.Windows.Automation.ScrollPattern]$scroll).SetScrollPercent(-1,0);$actionReport.invoked=$true
            Start-Sleep -Milliseconds 300;$list=ChatList
        } elseif ($rows[0].TryGetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern,[ref]$itemScroll)) {
            $report.scroll_method='ScrollItemPattern';$report.scroll_binding_verified=$true
            AssertRoot;$actionReport=[pscustomobject]@{action='scroll_chat_list';runtime_id=($rows[0].GetRuntimeId() -join ',');attempted=$true;invoked=$false}
            $report.actions+=@($actionReport)
            ([System.Windows.Automation.ScrollItemPattern]$itemScroll).ScrollIntoView();$actionReport.invoked=$true
            Start-Sleep -Milliseconds 300;$list=ChatList
        } else {
            $report.stage='scroll_bound_chat_list';$report.foreground_activation_requested=$true
            [ChatListWheel]::Activate([IntPtr]::new($handleValue),$expectedPid);$list=ChatList
            if (-not (FirstTwoVisible $list)) {
                $report.scroll_method='bound_mouse_wheel';$report.scroll_binding_verified=$true;$report.wheel_events=@()
                $actionReport=[pscustomobject]@{action='scroll_chat_list';runtime_id=[string]$payload.list_runtime_id;attempted=$true;invoked=$false}
                $report.actions+=@($actionReport)
                for ($i=0;$i -lt 12;$i++) {
                    $list=ChatList;if (FirstTwoVisible $list) { break }
                    $area=ListViewport $list;$x=[int][Math]::Floor($area.left+$area.width/2);$y=[int][Math]::Floor($area.top+$area.height/2)
                    [ChatListWheel]::Position([IntPtr]::new($handleValue),$expectedPid,$x,$y)
                    $area=GuardWheelPoint $list $x $y
                    $event=[pscustomobject]@{index=($i+1);list_runtime_id=[string]$payload.list_runtime_id;x=$x;y=$y;viewport=$area;delta=1200;
                        foreground_verified=$true;point_in_list_verified=$true;button_events=$false;keyboard_events=$false;attempted=$true;sent=$false}
                    $report.wheel_events+=@($event)
                    [ChatListWheel]::WheelUp([IntPtr]::new($handleValue),$expectedPid,$x,$y);$event.sent=$true
                    Start-Sleep -Milliseconds 200
                }
                $list=ChatList
                if (-not (FirstTwoVisible $list)) { throw 'Bounded upward scrolling did not expose both targets; stopped, no selection or invite.' }
                if (@($report.wheel_events).Count -eq 0) {
                    $report.actions=@($report.actions | Where-Object { $_.action -ne 'scroll_chat_list' });$report.scroll_method='none'
                }
                $actionReport.invoked=$true
            }
        }
    }
    if (-not (FirstTwoVisible $list)) { throw 'First two chat rows are not visible; preparation incomplete.' }
    Start-Sleep -Milliseconds 300;$search=ChatSearch;$list=ChatList
    if (($search.GetRuntimeId() -join ',') -ne $searchId -or -not (FirstTwoVisible $list)) { throw 'Ordinary chat page changed before final verification.' }
    $report.final_target_visibility=$report.target_visibility
    if (-not (ReadSearch $search 'final')) { throw 'Actual search changed during final verification.' }
    $report.first_two_visible=$true;$report.ok=$true;$report.state='ready';$report.stage='verified'
    $report | ConvertTo-Json -Depth 8 -Compress
} catch {
    $report.error=$_.Exception.Message
    $report | ConvertTo-Json -Depth 8 -Compress
    return
}
