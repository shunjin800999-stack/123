# One foreground list click only. Never invoke Add, Cancel, or any other button.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$attempted=$false; $sent=$false; $guardPath=$null
$searchAttempted=$false; $searchApplied=$false
$guardStage='';$fresh=$null;$regionChanges=@();$keepGuard=$false
try {
    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    if (-not ('MemberInput' -as [type])) {
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Runtime.InteropServices;
public static class MemberInput {
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
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
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll",SetLastError=true)] static extern uint SendInput(uint count,INPUT[] input,int size);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
    public static void SetDpi() { try { SetThreadDpiAwarenessContext(new IntPtr(-4)); } catch(EntryPointNotFoundException) {} }
    public static void Identity(IntPtr hwnd,int expectedPid) {
        uint pid;
        if (!IsWindow(hwnd)||IsIconic(hwnd)||GetWindowThreadProcessId(hwnd,out pid)==0||pid!=(uint)expectedPid)
            throw new Exception("Window identity changed or is minimized. Rescan.");
    }
    public static void Prepare(IntPtr hwnd,int pid) {
        Identity(hwnd,pid);
        if (GetForegroundWindow()!=hwnd) {
            SetForegroundWindow(hwnd);
            for (int i=0;i<10 && GetForegroundWindow()!=hwnd;i++) System.Threading.Thread.Sleep(50);
        }
        if (GetForegroundWindow()!=hwnd) throw new Exception("Cannot activate Telegram. Click the target Telegram window manually, then retry.");
    }
    public static void Foreground(IntPtr hwnd,int pid) {
        Identity(hwnd,pid);
        if (GetForegroundWindow()!=hwnd) throw new Exception("Foreground window changed. No click.");
    }
    public static void SameRow(string before,string current,int expectedWidth,int expectedHeight,int left,int top,int width,int height) {
        if (width<=0||height<=0||left<0||top<0||left+width>expectedWidth||top+height>expectedHeight)
            throw new Exception("Invalid verification crop.");
        using (Bitmap a=new Bitmap(before)) using (Bitmap b=new Bitmap(current)) {
            if(a.Width!=expectedWidth||b.Width!=expectedWidth||a.Height!=expectedHeight||b.Height!=expectedHeight)
                throw new Exception("Capture dimensions changed.");
            for(int y=top;y<top+height;y++) for(int x=left;x<left+width;x++)
                if(a.GetPixel(x,y).ToArgb()!=b.GetPixel(x,y).ToArgb())
                    throw new Exception("Contact row image changed. No click; inspect the current page.");
        }
    }
    public static void SameHeader(string before,string current,int left,int top,int width,int height,int exLeft,int exTop,int exWidth,int exHeight) {
        using(Bitmap a=new Bitmap(before)) using(Bitmap b=new Bitmap(current)) {
            if(width<=0||height<=0||left<0||top<0||left+width>a.Width||top+height>a.Height||a.Size!=b.Size)
                throw new Exception("Invalid selected header verification crop.");
            for(int y=top;y<top+height;y++) for(int x=left;x<left+width;x++) {
                // Search caret blinking is irrelevant; selected chip pixels are not.
                if(x>=exLeft&&x<exLeft+exWidth&&y>=exTop&&y<exTop+exHeight) continue;
                if(a.GetPixel(x,y).ToArgb()!=b.GetPixel(x,y).ToArgb())
                    throw new Exception("Selected header changed. No click.");
            }
        }
    }
    public static void Click(IntPtr hwnd,int pid,int x,int y) {
        Foreground(hwnd,pid);
        POINT p=new POINT(); p.X=x;p.Y=y;
        if(GetAncestor(WindowFromPoint(p),2)!=hwnd) throw new Exception("The click point is covered by another window.");
        foreach(int key in new int[]{1,2,4,16,17,18})
            if((GetAsyncKeyState(key)&0x8000)!=0) throw new Exception("Release mouse buttons and modifier keys before this test.");
        int vx=GetSystemMetrics(76),vy=GetSystemMetrics(77),vw=GetSystemMetrics(78),vh=GetSystemMetrics(79);
        if(vw<=1||vh<=1||x<vx||y<vy||x>=vx+vw||y>=vy+vh) throw new Exception("Point is outside the desktop.");
        INPUT[] input=new INPUT[3];
        input[0].data.mi.dx=(int)Math.Round((x-vx)*65535.0/(vw-1));
        input[0].data.mi.dy=(int)Math.Round((y-vy)*65535.0/(vh-1));
        input[0].data.mi.dwFlags=0x0001|0x8000|0x4000;
        input[1].data.mi.dwFlags=0x0002;input[2].data.mi.dwFlags=0x0004;
        uint n=SendInput(3,input,Marshal.SizeOf(typeof(INPUT)));
        if(n!=3) {
            // Release a possibly inserted down event; never retry the list click.
            INPUT[] release=new INPUT[1];release[0].data.mi.dwFlags=0x0004;
            SendInput(1,release,Marshal.SizeOf(typeof(INPUT)));
            throw new Exception("Input was not fully inserted. Check Telegram manually; do not retry blindly.");
        }
    }
}
'@
    }
    [MemberInput]::SetDpi()
    $payload=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
    $hwnd=[IntPtr]::new([Int64]::Parse($env:TG_INSPECT_HWND))
    $pidValue=[Int32]::Parse($env:TG_INSPECT_PID)
    $captureCode=[ScriptBlock]::Create([IO.File]::ReadAllText(
        (Join-Path $env:TG_ASSISTANT_DIR 'inspect_member_visual.ps1'),[Text.Encoding]::UTF8))
    function Capture([string]$path,[bool]$captureOnly) {
        $oldPayload=$env:TG_ACTION_PAYLOAD
        try {
            $capturePayload=@{image_path=$path;capture_only=$captureOnly}
            if (-not $captureOnly -and $payload.first_row_scan -eq $true) { $capturePayload.first_row_scan=$true }
            $env:TG_ACTION_PAYLOAD=$capturePayload | ConvertTo-Json -Compress
            return ((& $captureCode) | ConvertFrom-Json)
        } finally { $env:TG_ACTION_PAYLOAD=$oldPayload }
    }
    function MemberSearch($context) {
        $root=[System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
        if ($root.Current.ProcessId -ne $pidValue) { throw 'Search window identity changed.' }
        function OneClass($parent,[string]$className) {
            $condition=New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::ClassNameProperty,$className)
            $items=@($parent.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object {
                $_.Current.ProcessId -eq $pidValue -and -not $_.Current.IsOffscreen -and
                $_.Current.BoundingRectangle.Width -gt 0 -and $_.Current.BoundingRectangle.Height -gt 0
            })
            if ($items.Count -ne 1) { throw ('Search container is not unique: '+$className) }
            return $items[0]
        }
        $box=OneClass $root 'class Ui::BoxLayerWidget'
        if (($box.GetRuntimeId() -join ',') -ne $context.dialog_runtime_id) { throw 'Search dialog changed.' }
        $peer=OneClass $box 'class PeerListBox'
        $header=OneClass $peer 'class Ui::MultiSelect::Inner'
        $search=OneClass $header 'class Ui::InputField::Inner'
        if (-not $search.Current.IsEnabled -or $search.Current.ControlType -ne [System.Windows.Automation.ControlType]::Edit) {
            throw 'Member search field is unavailable.'
        }
        $value=$null
        if (-not $search.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value)) {
            throw 'Member search does not provide a writable ValuePattern. No selection click.'
        }
        $pattern=[System.Windows.Automation.ValuePattern]$value
        if ($pattern.Current.IsReadOnly) { throw 'Member search field is read-only.' }
        return $pattern
    }
    if ([string]$payload.number -notmatch '^[0-9]{1,6}$' -or [int]$payload.number -lt 0) { throw 'Invalid numeric name.' }
    if ($payload.mode -eq 'prepare') {
        [MemberInput]::Prepare($hwnd,$pidValue)
        $imagePath=[IO.Path]::GetFullPath([string]$payload.image_path)
        # Establish Add Members context before changing any input field.
        $context=Capture $imagePath $true
        if ($null -ne $payload.expected_dialog_id -and
            [string]$payload.expected_dialog_id -ne [string]$context.dialog_runtime_id) {
            throw 'Target group member dialog changed. No search or selection.'
        }
        $pattern=MemberSearch $context
        [MemberInput]::Foreground($hwnd,$pidValue)
        $searchAttempted=$true
        $pattern.SetValue([string]$payload.number)
        $readback=MemberSearch $context
        if ($readback.Current.Value -ne [string]$payload.number) { throw 'Member search value did not match the requested name.' }
        $searchApplied=$true
        Start-Sleep -Milliseconds 500
        $report=Capture $imagePath $false
        if ($report.dialog_runtime_id -ne $context.dialog_runtime_id) { throw 'Dialog changed while preparing search.' }
        $readback=MemberSearch $report
        if ($readback.Current.Value -ne [string]$payload.number) { throw 'Member search value changed while reading results.' }
        [MemberInput]::Foreground($hwnd,$pidValue)
        $report | Add-Member -NotePropertyName search_preparation -NotePropertyValue @{
            number=[string]$payload.number;search_attempted=$true;value_matches=$true
        }
        $report | ConvertTo-Json -Depth 12 -Compress
        return
    }
    if ($payload.mode -notin @('check','click_once')) { throw 'Unknown single-member operation.' }
    $guardStage='identity'
    [MemberInput]::Foreground($hwnd,$pidValue)
    $beforePath=[IO.Path]::GetFullPath([string]$payload.before_image_path)
    if ((Get-FileHash -LiteralPath $beforePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$payload.before_sha256) {
        throw 'The original evidence image changed. No click.'
    }
    if ($payload.guard_image_path) {
        $guardPath=[IO.Path]::GetFullPath([string]$payload.guard_image_path);$keepGuard=$true
        if ($guardPath -eq $beforePath -or (Test-Path -LiteralPath $guardPath)) { throw 'Guard image path must be new.' }
    } else {
        $guardPath=Join-Path ([IO.Path]::GetTempPath()) ('TGGuard-'+[Guid]::NewGuid().ToString('N')+'.png')
    }
    $fresh=Capture $guardPath $true
    if ($fresh.ok -ne $true -or $fresh.read_only -ne $true -or $fresh.scope -ne 'member_visual' -or
        $fresh.window_handle -ne $hwnd.ToInt64() -or $fresh.process_id -ne $pidValue -or
        $fresh.dialog_runtime_id -ne $payload.dialog_runtime_id -or $fresh.scale -ne 2) { throw 'Dialog identity changed. No click.' }
    $readback=MemberSearch $fresh
    if ($readback.Current.Value -ne [string]$payload.number) { throw 'Member search value changed. No click.' }
    foreach ($key in @('left','top','width','height')) {
        if ($fresh.capture.$key -ne $payload.capture.$key) { throw 'Dialog bounds changed. No click.' }
    }
    $guardStage='layout'
    foreach ($region in @('list','viewport','header','search')) {
        foreach ($key in @('left','top','width','height')) {
            if ($fresh.regions.$region.$key -ne $payload.regions.$region.$key) {
                $regionChanges+=@{region=$region;field=$key;before=$payload.regions.$region.$key;current=$fresh.regions.$region.$key}
            }
        }
    }
    # Loading more results can change only the list height. Reuse the OCR
    # result only if the exact target row still passes the pixel guard below.
    $listHeightOnly=($payload.target_member_only -eq $true -and $regionChanges.Count -eq 1 -and
        $regionChanges[0].region -eq 'list' -and $regionChanges[0].field -eq 'height' -and
        $regionChanges[0].before -gt 0 -and $regionChanges[0].current -gt 0)
    if ($regionChanges.Count -gt 0 -and -not $listHeightOnly) { throw 'List layout changed. No click.' }
    $guardStage='point'
    $x=[int]$payload.x;$y=[int]$payload.y;$n=$payload.name_bounds
    foreach ($v in @($n.left,$n.top,$n.width,$n.height)) {
        if ([double]::IsNaN([double]$v) -or [double]::IsInfinity([double]$v)) { throw 'Invalid name bounds.' }
    }
    if ($n.width -le 0 -or $n.height -le 0 -or
        $x -ne [int][Math]::Floor($n.left+$n.width/2) -or $y -ne [int][Math]::Floor($n.top+$n.height/2)) { throw 'Invalid name click point.' }
    function Inside($r,[double]$px,[double]$py) {
        return ($px -ge $r.left -and $px -lt ($r.left+$r.width) -and $py -ge $r.top -and $py -lt ($r.top+$r.height))
    }
    foreach ($region in @('list','viewport')) {
        $r=$fresh.regions.$region
        if (-not (Inside $r $x $y) -or $n.left -lt $r.left -or $n.top -lt $r.top -or
            ($n.left+$n.width) -gt ($r.left+$r.width) -or ($n.top+$n.height) -gt ($r.top+$r.height)) { throw 'Name is not fully inside the visible list.' }
    }
    foreach ($r in @($fresh.regions.header,$fresh.regions.search,$fresh.button_regions.add,$fresh.button_regions.cancel)) {
        if (Inside $r $x $y) { throw 'Point is inside a header or final button. No click.' }
    }
    $c=$fresh.capture;$l=$fresh.regions.list;$v=$fresh.regions.viewport
    $rowLeft=[Math]::Max($c.left,[Math]::Max($l.left,$v.left))
    $rowRight=[Math]::Min($c.left+$c.width,[Math]::Min($l.left+$l.width,$v.left+$v.width))
    $rowTop=[Math]::Max($l.top,[Math]::Max($v.top,$n.top-$n.height/2))
    $rowBottom=[Math]::Min($l.top+$l.height,[Math]::Min($v.top+$v.height,$n.top+$n.height*1.5))
    $pxLeft=[int][Math]::Floor(($rowLeft-$c.left)*2);$pxTop=[int][Math]::Floor(($rowTop-$c.top)*2)
    $pxRight=[int][Math]::Ceiling(($rowRight-$c.left)*2);$pxBottom=[int][Math]::Ceiling(($rowBottom-$c.top)*2)
    $guardStage='row'
    try {
        [MemberInput]::SameRow($beforePath,$guardPath,[int]($c.width*2),[int]($c.height*2),
            $pxLeft,$pxTop,($pxRight-$pxLeft),($pxBottom-$pxTop))
    } catch {
        $rowError=$_.Exception.GetBaseException().Message
        if ($listHeightOnly -and $rowError -eq 'Contact row image changed. No click; inspect the current page.') {
            # Nothing was clicked. Preserve the existing read-only refresh
            # when the target row itself changed while more results loaded.
            $guardStage='layout'
            throw 'List layout changed. No click.'
        }
        throw
    }
    if ($payload.target_member_only -ne $true) {
    $guardStage='header'
    $h=$fresh.regions.header;$s=$fresh.regions.search
    # The observed caret starts one screen pixel left/above InputField::Inner.
    # Permit at most two pixels, clipped to the header/capture; never mask a label.
    $margin=[double]$payload.search_caret_margin
    if ($margin -notin @(0,2)) { throw 'Invalid search caret margin.' }
    $maskLeft=[Math]::Max($c.left,[Math]::Max($h.left,$s.left-$margin))
    $maskTop=[Math]::Max($c.top,[Math]::Max($h.top,$s.top-$margin))
    $maskRight=[Math]::Min($c.left+$c.width,[Math]::Min($h.left+$h.width,$s.left+$s.width+$margin))
    $maskBottom=[Math]::Min($c.top+$c.height,[Math]::Min($h.top+$h.height,$s.top+$s.height+$margin))
    if ($margin -eq 2) {
        $protected=@($payload.selected_name_bounds | Where-Object {$null -ne $_})
        if ($null -eq $payload.selected_label_count -or [int]$payload.selected_label_count -lt 0 -or
            [int]$payload.selected_label_count -gt 40 -or $protected.Count -ne [int]$payload.selected_label_count) {
            throw 'Selected label protection is incomplete.'
        }
        foreach ($label in $protected) {
            foreach ($value in @($label.left,$label.top,$label.width,$label.height)) {
                if ($null -eq $value -or [double]::IsNaN([double]$value) -or [double]::IsInfinity([double]$value)) {
                    throw 'Invalid protected label bounds.'
                }
            }
            if ($label.width -le 0 -or $label.height -le 0 -or $label.left -lt $h.left -or $label.top -lt $h.top -or
                $label.left+$label.width -gt $h.left+$h.width -or $label.top+$label.height -gt $h.top+$h.height) {
                throw 'Protected label is not inside the header.'
            }
            if ($label.left -lt $maskRight -and $label.left+$label.width -gt $maskLeft -and
                $label.top -lt $maskBottom -and $label.top+$label.height -gt $maskTop) {
                throw 'Search caret mask overlaps a selected label. No click.'
            }
        }
    }
    [MemberInput]::SameHeader($beforePath,$guardPath,
        [int][Math]::Floor(($h.left-$c.left)*2),[int][Math]::Floor(($h.top-$c.top)*2),
        [int][Math]::Ceiling($h.width*2),[int][Math]::Ceiling($h.height*2),
        [int][Math]::Floor(($maskLeft-$c.left)*2),[int][Math]::Floor(($maskTop-$c.top)*2),
        ([int][Math]::Ceiling(($maskRight-$c.left)*2)-[int][Math]::Floor(($maskLeft-$c.left)*2)),
        ([int][Math]::Ceiling(($maskBottom-$c.top)*2)-[int][Math]::Floor(($maskTop-$c.top)*2)))
    }
    $guardStage='live'
    # Check the live UIA container again after pixel comparison, immediately before input.
    $root=[System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
    $condition=New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty,'class Ui::BoxLayerWidget')
    $boxes=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object {
        -not $_.Current.IsOffscreen -and $_.Current.BoundingRectangle.Width -gt 0
    })
    if ($root.Current.ProcessId -ne $pidValue -or $boxes.Count -ne 1 -or
        ($boxes[0].GetRuntimeId() -join ',') -ne $fresh.dialog_runtime_id) { throw 'Live dialog changed. No click.' }
    $b=$boxes[0].Current.BoundingRectangle
    if ([Math]::Floor($b.Left) -ne $c.left -or [Math]::Floor($b.Top) -ne $c.top -or
        ([Math]::Ceiling($b.Right)-[Math]::Floor($b.Left)) -ne $c.width -or
        ([Math]::Ceiling($b.Bottom)-[Math]::Floor($b.Top)) -ne $c.height) { throw 'Dialog moved. No click.' }
    if ($payload.mode -eq 'check') {
        @{ok=$true;mode='check';read_only=$true;guard_stable=$true;
            click_attempted=$false;click_sent=$false;number=[string]$payload.number;
            list_height_reused=$listHeightOnly;
            before_sha256=[string]$payload.before_sha256;guard_report=$fresh;
            final_invite_clicked=$false} | ConvertTo-Json -Depth 12 -Compress
        return
    }
    $attempted=$true
    [MemberInput]::Click($hwnd,$pidValue,$x,$y)
    $sent=$true
    @{ok=$true;mode='click_once';click_attempted=$true;click_sent=$true;
        number=[string]$payload.number;x=$x;y=$y;list_height_reused=$listHeightOnly;
        final_invite_clicked=$false} | ConvertTo-Json -Compress
} catch {
    @{ok=$false;error=$_.Exception.Message;mode=[string]$payload.mode;
        read_only=($payload.mode -eq 'check');guard_stable=$false;guard_stage=$guardStage;
        region_changes=@($regionChanges);guard_report=$fresh;
        click_attempted=$attempted;click_sent=$sent;
        search_attempted=$searchAttempted;search_applied=$searchApplied;
        final_invite_clicked=$false} | ConvertTo-Json -Depth 12 -Compress
    return
} finally {
    if (-not $keepGuard -and $null -ne $guardPath -and (Test-Path -LiteralPath $guardPath)) { Remove-Item -LiteralPath $guardPath -Force -ErrorAction SilentlyContinue }
}
