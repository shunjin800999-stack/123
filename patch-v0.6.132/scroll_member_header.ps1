# Bounded selected-header scrolling only. No click, search, key input or DB access.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$report=@{ok=$false;read_only=$false;scope='member_header_scroll_scan';scan_pass_count=1;scan_mode='single_downward_page_scan';passes=@();wheel_events=@();hit_tests=@();surface_checks=@();point_guard_version=4;
    search_changed=$false;selection_click_sent=$false;final_invite_clicked=$false;database_updated=$false;state='preparing'}
$journal=$null
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Drawing
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public static class MemberHeaderWheel {
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
    public static void RequireForeground(IntPtr hwnd,int pid) {
        Identity(hwnd,pid);IdleKeys();
        if(GetForegroundWindow()!=hwnd) throw new Exception("Foreground changed; preserve selection, no wheel.");
    }
    public static void Position(IntPtr hwnd,int pid,int x,int y) {
        Identity(hwnd,pid);IdleKeys();
        if(GetForegroundWindow()!=hwnd) throw new Exception("Foreground changed; no wheel.");
        POINT p=new POINT();p.X=x;p.Y=y;
        if(GetAncestor(WindowFromPoint(p),2)!=hwnd) throw new Exception("List point is covered by another window; no wheel.");
        if(!SetCursorPos(x,y)) throw new Exception("Cannot position pointer inside verified chat list.");
    }
    public static long[] ConfirmHeaderSurface(IntPtr hwnd,int pid,string reference,string output,
        int left,int top,int width,int height,int hx,int hy,int hw,int hh,int sx,int sy,int sw,int sh) {
        RequireForeground(hwnd,pid);
        if(width<=0||height<=0||width>3000||height>3000||hx<0||hy<0||hw<=0||hh<=0||hx+hw>width||hy+hh>height)
            throw new Exception("Invalid visible header surface.");
        using(Bitmap frame=new Bitmap(reference))
        using(Bitmap screen=new Bitmap(width,height,PixelFormat.Format24bppRgb))
        using(Bitmap enlarged=new Bitmap(width*2,height*2,PixelFormat.Format24bppRgb)) {
            if(frame.Width!=width*2||frame.Height!=height*2) throw new Exception("Header surface capture size changed.");
            using(Graphics g=Graphics.FromImage(screen)) {
                g.CopyFromScreen(left,top,0,0,new Size(width,height),CopyPixelOperation.SourceCopy);
            }
            using(Graphics g=Graphics.FromImage(enlarged)) {
                g.InterpolationMode=System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                g.DrawImage(screen,new Rectangle(0,0,width*2,height*2));
            }
            enlarged.Save(output,ImageFormat.Png);
            long compared=0,different=0;
            BitmapData aData=null,bData=null;
            try {
                Rectangle bounds=new Rectangle(0,0,width*2,height*2);
                aData=frame.LockBits(bounds,ImageLockMode.ReadOnly,PixelFormat.Format24bppRgb);
                bData=enlarged.LockBits(bounds,ImageLockMode.ReadOnly,PixelFormat.Format24bppRgb);
                byte[] aRow=new byte[width*2*3],bRow=new byte[width*2*3];
                for(int y=hy*2;y<(hy+hh)*2;y++) {
                    Marshal.Copy(IntPtr.Add(aData.Scan0,y*aData.Stride),aRow,0,aRow.Length);
                    Marshal.Copy(IntPtr.Add(bData.Scan0,y*bData.Stride),bRow,0,bRow.Length);
                    for(int x=hx*2;x<(hx+hw)*2;x++) {
                        // Exclude only the live search input and its blinking caret.
                        if(x>=(sx-2)*2&&x<(sx+sw+2)*2&&y>=(sy-2)*2&&y<(sy+sh+2)*2) continue;
                        compared++;int i=x*3;
                        if(Math.Abs((int)aRow[i]-bRow[i])>3||Math.Abs((int)aRow[i+1]-bRow[i+1])>3||
                            Math.Abs((int)aRow[i+2]-bRow[i+2])>3) different++;
                    }
                }
            } finally {
                if(aData!=null) frame.UnlockBits(aData);
                if(bData!=null) enlarged.UnlockBits(bData);
            }
            RequireForeground(hwnd,pid);
            return new long[]{compared,different};
        }
    }
    public static void Wheel(IntPtr hwnd,int pid,int x,int y,int delta) {
        Identity(hwnd,pid);IdleKeys();POINT p;
        if(GetForegroundWindow()!=hwnd||!GetCursorPos(out p)||p.X!=x||p.Y!=y||GetAncestor(WindowFromPoint(p),2)!=hwnd)
            throw new Exception("Pointer or foreground changed; no wheel.");
        INPUT[] input=new INPUT[1];input[0].data.mi.dwFlags=0x0800;input[0].data.mi.mouseData=unchecked((uint)delta);
        if(SendInput(1,input,Marshal.SizeOf(typeof(INPUT)))!=1)
            throw new Exception("Wheel insertion uncertain; no automatic retry.");
    }
}
'@
    [MemberHeaderWheel]::SetDpi()
    $payload=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
    $handleValue=[Int64]::Parse($env:TG_INSPECT_HWND);$expectedPid=[Int32]::Parse($env:TG_INSPECT_PID)
    $hwnd=[IntPtr]::new($handleValue)
    $source=$payload.source;$frozen=$source.header_scroll
    if ($null -eq $frozen -or $source.scope -ne 'member_visual' -or $source.read_only -ne $true -or
        $source.ok -ne $true -or $source.window_handle -ne $handleValue -or $source.process_id -ne $expectedPid -or
        [string]::IsNullOrWhiteSpace([string]$source.dialog_runtime_id)) { throw 'Missing live header source.' }
    $prefix=[IO.Path]::GetFullPath([string]$payload.prefix)
    $journal=$prefix+'_native.json';$report.report_path=$journal
    $expectedPath=[IO.Path]::GetFullPath([string]$payload.executable_path)
    $report.window_handle=$handleValue;$report.process_id=$expectedPid;$report.dialog_runtime_id=[string]$source.dialog_runtime_id
    $process=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = '+$expectedPid)
    if ($null -eq $process -or [string]::IsNullOrEmpty($process.ExecutablePath) -or
        -not [string]::Equals([IO.Path]::GetFullPath($process.ExecutablePath),$expectedPath,[StringComparison]::OrdinalIgnoreCase)) { throw 'Executable binding changed.' }
    $report.executable_path=$expectedPath;$report.process_path_verified=$true
    $walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
    function SaveJournal {
        $json=$report | ConvertTo-Json -Depth 20 -Compress
        [IO.File]::WriteAllText($journal+'.tmp',$json,[Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath ($journal+'.tmp') -Destination $journal -Force
    }
    function Visible($e) {
        $c=$e.Current;$b=$c.BoundingRectangle
        return ($c.ProcessId -eq $expectedPid -and -not $c.IsOffscreen -and $b.Width -gt 0 -and $b.Height -gt 0)
    }
    function OneClass($parent,[string]$name,[bool]$allowOffscreen=$false) {
        $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,$name)
        $all=@($parent.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object { (Visible $_) -or ($allowOffscreen -and $_.Current.ProcessId -eq $expectedPid -and $_.Current.BoundingRectangle.Width -gt 0) })
        if ($all.Count -ne 1) { throw ('Header tree ambiguous: '+$name) };return $all[0]
    }
    function RectOf($e) {
        $b=$e.Current.BoundingRectangle;return @{left=$b.Left;top=$b.Top;width=$b.Width;height=$b.Height}
    }
    function SameRect($a,$b) {
        foreach ($key in @('left','top','width','height')) { if ($a.$key -ne $b.$key) {return $false} };return $true
    }
    function State {
        [MemberHeaderWheel]::RequireForeground($hwnd,$expectedPid)
        $root=[System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
        if ($root.Current.ProcessId -ne $expectedPid -or $root.Current.ClassName -ne 'class MainWindow') { throw 'Root changed.' }
        $box=OneClass $root 'class Ui::BoxLayerWidget'
        if (($box.GetRuntimeId() -join ',') -cne [string]$source.dialog_runtime_id -or -not (SameRect (RectOf $box) $source.capture)) { throw 'Member dialog changed.' }
        $peer=OneClass $box 'class PeerListBox';$inner=OneClass $peer 'class Ui::MultiSelect::Inner'
        $viewport=$walker.GetParent($inner);$scroll=$walker.GetParent($viewport);$multi=$walker.GetParent($scroll)
        if ($viewport.Current.ClassName -ne 'class QWidget' -or $scroll.Current.ClassName -ne 'class Ui::ScrollArea' -or
            $multi.Current.ClassName -ne 'class Ui::MultiSelect') { throw 'Header scroll ancestry changed.' }
        $search=OneClass $inner 'class Ui::InputField::Inner' $true;$value=$null
        if (-not $search.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value) -or
            [string]$value.Current.Value -cne [string]$frozen.search_value) { throw 'Search value changed; preserve current selection.' }
        foreach ($pair in @(@($inner,'inner_runtime_id'),@($viewport,'viewport_runtime_id'),@($scroll,'scroll_runtime_id'),@($search,'search_runtime_id'))) {
            if (($pair[0].GetRuntimeId() -join ',') -cne [string]$frozen.($pair[1])) { throw 'Header control identity changed.' }
        }
        $i=RectOf $inner;$v=RectOf $viewport
        if (-not (SameRect $v $frozen.viewport) -or $i.left -ne $v.left -or $i.width -ne $v.width -or
            $i.height -ne $frozen.inner.height -or $i.top -gt $v.top -or $i.top+$i.height -lt $v.top+$v.height -or
            $v.height -lt 64 -or $i.height -gt 1000) { throw 'Header layout or selection count changed.' }
        $report.header_binding_verified=$true
        return @{inner=$inner;viewport=$viewport;scroll=$scroll;search_rect=(RectOf $search);offset=($v.top-$i.top);max_offset=($i.height-$v.height);rect=$v}
    }
    $captureScript=[ScriptBlock]::Create([IO.File]::ReadAllText((Join-Path $env:TG_ASSISTANT_DIR 'inspect_member_visual.ps1')))
    function Capture([string]$name,[bool]$captureOnly) {
        $state=State;$path=$prefix+'_'+$name+'.png'
        $originalPayload=$env:TG_ACTION_PAYLOAD
        try {
            $env:TG_ACTION_PAYLOAD=@{image_path=$path;capture_only=$captureOnly} | ConvertTo-Json -Compress
            $frame=((& $captureScript) -join "`n") | ConvertFrom-Json
        } finally { $env:TG_ACTION_PAYLOAD=$originalPayload }
        if ($frame.ok -ne $true -or $frame.dialog_runtime_id -cne [string]$source.dialog_runtime_id -or
            $frame.header_scroll.offset -ne $state.offset) { throw 'Scroll frame changed during capture.' }
        $after=State
        if ($after.offset -ne $state.offset) { throw 'Header moved during capture.' }
        return $frame
    }
    function Inside($r,[int]$x,[int]$y) {
        return ($r.width -gt 0 -and $r.height -gt 0 -and $x -ge $r.left -and $y -ge $r.top -and
            $x -lt $r.left+$r.width -and $y -lt $r.top+$r.height)
    }
    function ProbeHeaderPoint([int]$x,[int]$y,$state) {
        $probe=@{x=$x;y=$y;offset=$state.offset;viewport=$state.rect;search=$state.search_rect;paths=@();verified=$false;search_excluded=$false}
        if (-not (Inside $state.rect $x $y) -or (Inside $state.search_rect $x $y)) {return $probe}
        $probe.search_excluded=$true
        $point=[System.Windows.Point]::new($x,$y)
        $under=[System.Windows.Automation.AutomationElement]::FromPoint($point)
        foreach ($kind in @('raw','control')) {
            $hitWalker=if ($kind -eq 'raw') {[System.Windows.Automation.TreeWalker]::RawViewWalker} else {$walker}
            $element=$under;$nodes=@();$allBound=$true;$target=$null
            try {
                for ($depth=0;$null -ne $element -and $depth -lt 24;$depth++) {
                    $current=$element.Current
                    $node=@{runtime_id=($element.GetRuntimeId() -join ',');class_name=[string]$current.ClassName;
                        process_id=$current.ProcessId;bounds=(RectOf $element)}
                    $nodes+=@($node)
                    if ($node.process_id -ne $expectedPid) {$allBound=$false;break}
                    $matches=(($node.runtime_id -ceq [string]$frozen.inner_runtime_id -and $node.class_name -ceq 'class Ui::MultiSelect::Inner') -or
                        ($node.runtime_id -ceq [string]$frozen.viewport_runtime_id -and $node.class_name -ceq 'class QWidget') -or
                        ($node.runtime_id -ceq [string]$frozen.scroll_runtime_id -and $node.class_name -ceq 'class Ui::ScrollArea'))
                    if ($matches -and (Inside $node.bounds $x $y)) {$target=$node;break}
                    $element=$hitWalker.GetParent($element)
                }
                $probe.paths+=@(@{walker=$kind;nodes=$nodes})
                if ($allBound -and $null -ne $target) {
                    $probe.verified=$true;$probe.bound_runtime_id=$target.runtime_id;$probe.bound_class=$target.class_name;break
                }
            } catch { $probe.paths+=@(@{walker=$kind;nodes=$nodes;error=$_.Exception.Message}) }
        }
        return $probe
    }
    function QtHistoryBinding {
        $root=[System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
        if ($root.Current.ProcessId -ne $expectedPid -or $root.Current.ClassName -cne 'class MainWindow') {
            throw 'Ordinary history root changed.'
        }
        $mainId=$root.GetRuntimeId() -join ','
        $classes=@('class HistoryInner','class Ui::ElasticScroll','class HistoryWidget')
        # ElasticScroll is not unique in a Telegram window. Locate the unique
        # HistoryInner once, then follow its actual raw parents, never siblings.
        $rawWalker=[System.Windows.Automation.TreeWalker]::RawViewWalker
        $element=OneClass $root 'class HistoryInner';$items=@();$ancestors=@();$rootReached=$false
        for ($depth=0;$null -ne $element -and $depth -lt 24;$depth++) {
            $current=$element.Current
            $node=@{runtime_id=($element.GetRuntimeId() -join ',');class_name=[string]$current.ClassName;
                process_id=$current.ProcessId}
            if ($node.process_id -ne $expectedPid) {throw 'Ordinary history left the bound process.'}
            if ($depth -lt 3) {
                if ($node.class_name -cne $classes[$depth] -or -not (Visible $element)) {
                    throw 'Ordinary history tree is not the observed Qt branch.'
                }
                $items+=@(@{runtime_id=$node.runtime_id;class_name=$node.class_name})
            }
            $ancestors+=@($node)
            if ($node.runtime_id -ceq $mainId) {
                if ($depth -lt 3 -or $node.class_name -cne 'class MainWindow') {throw 'Ordinary history root is invalid.'}
                $rootReached=$true;break
            }
            $element=$rawWalker.GetParent($element)
        }
        if (-not $rootReached) {throw 'Ordinary history ancestry does not reach the bound window.'}
        $binding=@{binding_method='history_ancestors';main_runtime_id=$mainId;controls=$items;ancestor_nodes=$ancestors}
        if ($report.ContainsKey('qt_history_binding')) {
            $old=$report.qt_history_binding
            if ($old.main_runtime_id -cne $mainId -or
                [string]::Join(',',@($old.ancestor_nodes | ForEach-Object {$_.runtime_id})) -cne [string]::Join(',',@($ancestors | ForEach-Object {$_.runtime_id}))) {
                throw 'Underlying history changed during header read.'
            }
        } else {$report.qt_history_binding=$binding}
        return $report.qt_history_binding
    }
    function BindQtModalGeometry($probe,$state) {
        $binding=QtHistoryBinding;$matched=$false
        foreach ($path in $probe.paths) {
            if ($path.walker -ne 'raw' -or $path.ContainsKey('error')) {continue}
            $nodes=@($path.nodes);$ancestors=@($binding.ancestor_nodes)
            if ($nodes.Count -lt $ancestors.Count) {continue}
            $starts=@(0..($nodes.Count-1) | Where-Object { $nodes[$_].runtime_id -ceq $ancestors[0].runtime_id })
            if ($starts.Count -ne 1) {continue}
            $start=$starts[0]
            if ($start -gt 6 -or $nodes.Count-$start -lt $ancestors.Count) {continue}
            $good=$true
            for ($i=0;$i -lt $start;$i++) {
                if ($nodes[$i].process_id -ne $expectedPid -or -not $nodes[$i].runtime_id) {$good=$false;break}
            }
            for ($i=0;$i -lt $ancestors.Count;$i++) {
                if ($nodes[$start+$i].process_id -ne $expectedPid -or $nodes[$start+$i].runtime_id -cne $ancestors[$i].runtime_id -or
                    $nodes[$start+$i].class_name -cne $ancestors[$i].class_name) {$good=$false;break}
            }
            if ($good) {$matched=$true;break}
        }
        if (-not $matched -or $probe.search_excluded -ne $true) {return $probe}
        $probe.mode='qt_modal_surface';$probe.geometry_bound=$true;$probe.qt_history_binding=$binding
        $probe.bound_runtime_id=[string]$frozen.viewport_runtime_id;$probe.bound_class='class QWidget'
        return $probe
    }
    function ConfirmQtModalSurface($probe,$state,[string]$suffix) {
        $probe.verified=$false;$probe.surface_attempts=@()
        $report.surface_checks+=@($probe)
        # Qt scrollbar opacity may change between PrintWindow and CopyFromScreen.
        # Re-capture only, with the original strict comparison and no wheel retry.
        for ($attempt=1;$attempt -le 3;$attempt++) {
            if ($attempt -gt 1) {Start-Sleep -Milliseconds 220}
            $now=State
            if ($now.offset -ne $state.offset) {throw 'Header moved while waiting for surface stability.'}
            $fresh=BindQtModalGeometry (ProbeHeaderPoint ([int]$probe.x) ([int]$probe.y) $now) $now
            if ($fresh.geometry_bound -ne $true -or $fresh.bound_runtime_id -cne $probe.bound_runtime_id) {
                throw 'Header point changed during surface confirmation. No wheel sent.'
            }
            $probe.paths=$fresh.paths;$probe.qt_history_binding=$fresh.qt_history_binding
            $attemptSuffix=if ($attempt -eq 1) {$suffix} else {$suffix+'_attempt'+$attempt}
            $guard=Capture ('surface_'+$attemptSuffix) $true
            if ($guard.header_scroll.offset -ne $state.offset) {throw 'Header offset changed before visible surface confirmation.'}
            $c=$guard.capture;$h=$guard.regions.header;$s=$guard.regions.search
            $screenPath=$prefix+'_screen_'+$attemptSuffix+'.png'
            $metrics=[MemberHeaderWheel]::ConfirmHeaderSurface($hwnd,$expectedPid,[string]$guard.image_path,$screenPath,
                [int]$c.left,[int]$c.top,[int]$c.width,[int]$c.height,
                [int]($h.left-$c.left),[int]($h.top-$c.top),[int]$h.width,[int]$h.height,
                [int]($s.left-$c.left),[int]($s.top-$c.top),[int]$s.width,[int]$s.height)
            $surface=@{ok=$false;attempt_index=$attempt;compared_pixels=$metrics[0];different_pixels=$metrics[1];
                reference_image_path=[string]$guard.image_path;screen_image_path=$screenPath;
                reference_sha256=(Get-FileHash -LiteralPath $guard.image_path -Algorithm SHA256).Hash.ToLowerInvariant();
                screen_sha256=(Get-FileHash -LiteralPath $screenPath -Algorithm SHA256).Hash.ToLowerInvariant();
                frame=@{window_handle=$handleValue;process_id=$expectedPid;dialog_runtime_id=[string]$source.dialog_runtime_id;
                    capture=$guard.capture;header_scroll=$guard.header_scroll};header=$h;search=$s;foreground_verified=$true}
            $probe.surface=$surface;$probe.surface_attempts+=@($surface)
            $now=State
            if ($now.offset -ne $state.offset) {SaveJournal;throw 'Header moved during surface comparison.'}
            if ($metrics[0] -lt 1000) {SaveJournal;throw 'Insufficient visible header surface. No wheel sent.'}
            if ($metrics[1]/[double]$metrics[0] -le 0.005) {
                $surface.ok=$true;$probe.verified=$true;return $probe
            }
            SaveJournal
        }
        throw 'Visible header differs after three captures. No wheel sent.'
    }
    function RecheckPoint([int]$x,[int]$y,$state,$previous,[string]$suffix) {
        $probe=ProbeHeaderPoint $x $y $state
        if ($previous.mode -eq 'qt_modal_surface' -and $probe.verified -ne $true) {
            $probe=BindQtModalGeometry $probe $state
            if ($probe.geometry_bound -eq $true) {$probe=ConfirmQtModalSurface $probe $state $suffix}
        }
        return $probe
    }
    function Wheel([int]$delta,[string]$phase) {
        if (@($report.wheel_events).Count -ge 96) { throw 'Header wheel limit reached.' }
        $state=State;$v=$state.rect
        # Qt point lookup can skip QWidget in ControlView or miss painted blank space.
        # Try a bounded set of live viewport positions, accepting only exact bound
        # Inner / viewport / scroll IDs in RawView or ControlView. No modal/root fallback.
        $candidates=@(@(0.25,0.50),@(0.50,0.50),@(0.75,0.50),@(0.25,0.25),@(0.50,0.25),@(0.75,0.25),@(0.50,0.75))
        $chosen=$null
        foreach ($candidate in $candidates) {
            $x=[int][Math]::Floor($v.left+$v.width*($candidate[0]));$y=[int][Math]::Floor($v.top+$v.height*($candidate[1]))
            $probe=ProbeHeaderPoint $x $y $state
            $report.hit_tests+=@($probe)
            if ($probe.verified -eq $true) {$chosen=$probe;break}
        }
        if ($null -eq $chosen) {
            foreach ($candidate in $report.hit_tests) {
                if ($candidate.offset -ne $state.offset -or $candidate.search_excluded -ne $true) {continue}
                $alternative=BindQtModalGeometry $candidate $state
                if ($alternative.geometry_bound -eq $true) {$chosen=$alternative;break}
            }
        }
        if ($null -eq $chosen) {SaveJournal;throw 'No bound header or supported Qt modal surface; see hit_tests. No wheel sent.'}

        $x=[int]$chosen.x;$y=[int]$chosen.y
        [MemberHeaderWheel]::Position($hwnd,$expectedPid,$x,$y)
        $confirmed=RecheckPoint $x $y (State) $chosen ('position'+(@($report.wheel_events).Count+1))
        $report.hit_tests+=@($confirmed)
        if ($confirmed.verified -ne $true -or $confirmed.bound_runtime_id -cne $chosen.bound_runtime_id) {
            SaveJournal;throw 'Header point changed after pointer positioning. No wheel sent.'
        }
        $event=@{index=(@($report.wheel_events).Count+1);delta=$delta;phase=$phase;x=$x;y=$y;before_offset=$state.offset;
            viewport_runtime_id=[string]$frozen.viewport_runtime_id;requested=$true;sent=$false;binding_verified=$true;point_proof=$confirmed}
        $report.wheel_events+=@($event);$report.state='wheel_requested';SaveJournal
        $immediate=State
        if ($immediate.offset -ne $state.offset) { throw 'Header changed before wheel insertion.' }
        $lastHit=RecheckPoint $x $y $immediate $confirmed ('prewheel'+(@($report.wheel_events).Count))
        if ($lastHit.verified -ne $true -or $lastHit.bound_runtime_id -cne $confirmed.bound_runtime_id) {
            $event.point_recheck=$lastHit;SaveJournal;throw 'Header point changed immediately before wheel. No wheel sent.'
        }
        $event.point_proof=$lastHit
        [MemberHeaderWheel]::Wheel($hwnd,$expectedPid,$x,$y,$delta);$event.sent=$true
        # Leave the chips before capture so hover styling is not mistaken for text.
        $parkX=[int][Math]::Floor($source.capture.left+$source.capture.width-12)
        $parkY=[int][Math]::Floor($source.capture.top+16)
        [MemberHeaderWheel]::Position($hwnd,$expectedPid,$parkX,$parkY)
        $event.pointer_parked=$true
        Start-Sleep -Milliseconds 180
        $after=State;$event.after_offset=$after.offset
        if (($delta -gt 0 -and $after.offset -gt $state.offset) -or ($delta -lt 0 -and $after.offset -lt $state.offset)) { throw 'Header scrolled in the wrong direction.' }
        $report.state='reading';SaveJournal
        return $after
    }
    [MemberHeaderWheel]::Activate($hwnd,$expectedPid)
    $initial=State
    if ($initial.offset -ne $frozen.offset) { throw 'Source scroll offset is stale; no wheel.' }
    $report.state='reading';SaveJournal
    for ($pass=1;$pass -le 1;$pass++) {
        $state=State;$idle=0
        while ($state.offset -gt 0) {
            $before=$state.offset;$state=Wheel 1200 'position_top'
            if ($state.offset -eq $before) {$idle++} else {$idle=0}
            if ($idle -ge 4) { throw 'Header did not reach top; no guess or selection click.' }
        }
        $pages=@((Capture ('pass'+$pass+'_page0') $true));$page=0;$idle=0
        while ($state.offset -lt $state.max_offset) {
            $before=$state.offset;$step=if ($state.rect.height-44 -ge 60) {-120} else {-40};$state=Wheel $step 'read_down'
            if ($state.offset -eq $before) {
                $idle++;if ($idle -ge 4) { throw 'Header did not reach bottom; preserve selection.' };continue
            }
            if ($state.offset-$before -gt $state.rect.height-44) { throw 'Header page overlap is too small; stop before selecting.' }
            $idle=0;$page++
            $pages+=@((Capture ('pass'+$pass+'_page'+$page) $true))
            if ($page -ge 24) {throw 'Header page limit reached.'}
        }
        $report.passes+=@(@{index=$pass;pages=$pages});SaveJournal
    }
    $report.final=Capture 'final' $false
    if ($report.final.header_scroll.offset -ne $report.final.header_scroll.max_offset) {throw 'Header final capture is not at bottom.'}
    $report.state='captured';$report.ok=$true;SaveJournal
    $report | ConvertTo-Json -Depth 20 -Compress
} catch {
    $report.ok=$false;$report.state='review';$report.error=$_.Exception.Message
    if ($null -ne $journal) { try {SaveJournal} catch {} }
    $report | ConvertTo-Json -Depth 20 -Compress
    exit 1
}
