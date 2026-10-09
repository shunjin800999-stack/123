# Read-only visual diagnostic. No focus, input, selection or button invocation.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Drawing
    if (-not ('DialogCapture' -as [type])) {
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public static class DialogCapture {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
    public static void SetDpi() {
        try { SetThreadDpiAwarenessContext(new IntPtr(-4)); }
        catch (EntryPointNotFoundException) { SetProcessDPIAware(); }
    }
    public static void Save(IntPtr hwnd, int left, int top, int width, int height, string path) {
        RECT r;
        if (!GetWindowRect(hwnd, out r)) throw new Exception("Cannot read window bounds.");
        int w = r.Right-r.Left, h = r.Bottom-r.Top;
        if (w<=0 || h<=0 || (long)w*h>20000000) throw new Exception("Invalid window size.");
        Rectangle crop = new Rectangle(left-r.Left, top-r.Top, width, height);
        if (crop.Left<0 || crop.Top<0 || crop.Right>w || crop.Bottom>h)
            throw new Exception("Dialog extends outside the selected window.");
        using (Bitmap full = new Bitmap(w, h, PixelFormat.Format24bppRgb)) {
            using (Graphics g = Graphics.FromImage(full)) {
                IntPtr dc = g.GetHdc();
                bool success;
                try { success = PrintWindow(hwnd, dc, 2); }
                finally { g.ReleaseHdc(dc); }
                if (!success) throw new Exception("Window capture failed.");
            }
            using (Bitmap dialog = full.Clone(crop, PixelFormat.Format24bppRgb))
            using (Bitmap enlarged = new Bitmap(width*2, height*2, PixelFormat.Format24bppRgb)) {
                using (Graphics g = Graphics.FromImage(enlarged)) {
                    g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                    g.DrawImage(dialog, new Rectangle(0,0,width*2,height*2));
                }
                enlarged.Save(path, ImageFormat.Png);
            }
        }
    }
    public static void SaveCrop(string sourcePath, string outputPath, int width, int height) {
        using (Bitmap source = new Bitmap(sourcePath)) {
            if (width!=source.Width || height<=0 || height>source.Height)
                throw new Exception("Invalid first-row OCR crop.");
            using (Bitmap crop = source.Clone(new Rectangle(0,0,width,height), PixelFormat.Format24bppRgb)) {
                crop.Save(outputPath, ImageFormat.Png);
            }
        }
    }
    public static void SaveHeader(string sourcePath, string outputPath, int left, int top,
        int width, int height, int maxDimension) {
        if (width<=0 || height<=0 || (long)width*2>maxDimension || (long)height*2>maxDimension)
            throw new Exception("Header diagnostic exceeds the OCR size limit.");
        using (Bitmap source = new Bitmap(sourcePath)) {
            Rectangle crop = new Rectangle(left, top, width, height);
            if (left<0 || top<0 || crop.Right>source.Width || crop.Bottom>source.Height)
                throw new Exception("Selected header is outside the captured dialog.");
            using (Bitmap header = source.Clone(crop, PixelFormat.Format24bppRgb))
            using (Bitmap enlarged = new Bitmap(width*2, height*2, PixelFormat.Format24bppRgb)) {
                using (Graphics g = Graphics.FromImage(enlarged)) {
                    g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                    g.DrawImage(header, new Rectangle(0,0,width*2,height*2));
                }
                enlarged.Save(outputPath, ImageFormat.Png);
            }
        }
    }
}
'@
    }
    [DialogCapture]::SetDpi()
    $payload = $env:TG_ACTION_PAYLOAD | ConvertFrom-Json
    $path = [IO.Path]::GetFullPath([string]$payload.image_path)
    if ([IO.Path]::GetExtension($path) -ne '.png') { throw 'The capture must be a PNG file.' }
    $handleValue = [Int64]::Parse($env:TG_INSPECT_HWND)
    $expectedPid = [Int32]::Parse($env:TG_INSPECT_PID)
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    if ($null -eq $root -or $root.Current.ProcessId -ne $expectedPid) { throw 'Window identity changed. Rescan.' }
    function Visible($e) {
        $c=$e.Current; $b=$c.BoundingRectangle
        return ($c.ProcessId -eq $expectedPid -and -not $c.IsOffscreen -and
            $b.Width -gt 0 -and $b.Height -gt 0 -and
            -not [double]::IsInfinity($b.Left) -and -not [double]::IsInfinity($b.Top) -and
            -not [double]::IsInfinity($b.Width) -and -not [double]::IsInfinity($b.Height) -and
            -not [double]::IsNaN($b.Left) -and -not [double]::IsNaN($b.Top))
    }
    function FindClass($parent, [string]$name) {
        $condition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ClassNameProperty, $name)
        foreach ($e in $parent.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)) {
            if (Visible $e) { $e }
        }
    }
    function Single($elements, [string]$description) {
        $all=@($elements)
        if ($all.Count -ne 1) { throw ('Expected one '+$description+', found '+$all.Count+'.') }
        return $all[0]
    }
    function RectOf($e) {
        $b=$e.Current.BoundingRectangle
        return @{left=$b.Left;top=$b.Top;width=$b.Width;height=$b.Height}
    }
    $box=Single @(FindClass $root 'class Ui::BoxLayerWidget') 'dialog container'
    $peer=Single @(FindClass $box 'class PeerListBox') 'member selection box'
    $labels=@(FindClass $box 'class Ui::FlatLabel' | Where-Object { $_.Current.Name -in @('Add Members','添加成员','Adicionar Membros','Adicionar membros','Adicionar Participantes','Adicionar participantes') })
    if ($labels.Count -ne 1) { throw 'Keep Add Members open. Other dialog types are not supported.' }
    $buttons=@($box.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button))))
    $add=@($buttons | Where-Object { (Visible $_) -and $_.Current.Name -in @('Add','添加','Adicionar') })
    $cancel=@($buttons | Where-Object { (Visible $_) -and $_.Current.Name -in @('Cancel','取消','Cancelar') })
    if ($add.Count -ne 1 -or $cancel.Count -ne 1) { throw 'Member dialog buttons are not unique.' }
    $list=Single @(FindClass $peer 'class PeerListContent') 'contact list'
    $header=Single @(FindClass $peer 'class Ui::MultiSelect::Inner') 'selected member header'
    # The search may be below the clipped viewport while reading upper chips.
    $searchCondition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,'class Ui::InputField::Inner')
    $search=Single @($header.FindAll([System.Windows.Automation.TreeScope]::Descendants,$searchCondition) |
        Where-Object { $_.Current.ProcessId -eq $expectedPid -and $_.Current.BoundingRectangle.Width -gt 0 }) 'search field'
    $walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
    $viewport=$walker.GetParent($list)
    if ($null -eq $viewport -or $viewport.Current.ClassName -ne 'class QWidget') { throw 'List viewport is not recognized.' }
    # Native report: Inner -> QWidget viewport -> ScrollArea -> MultiSelect.
    $headerViewport=$walker.GetParent($header)
    $headerScroll=$walker.GetParent($headerViewport)
    $multi=$walker.GetParent($headerScroll)
    if ($null -eq $headerViewport -or $headerViewport.Current.ClassName -ne 'class QWidget' -or
        $null -eq $headerScroll -or $headerScroll.Current.ClassName -ne 'class Ui::ScrollArea' -or
        $null -eq $multi -or $multi.Current.ClassName -ne 'class Ui::MultiSelect') { throw 'Selected header scroll tree is not recognized.' }
    $hr=$header.Current.BoundingRectangle;$hv=$headerViewport.Current.BoundingRectangle
    if ($hr.Left -ne $hv.Left -or $hr.Width -ne $hv.Width -or $hr.Top -gt $hv.Top -or
        $hr.Bottom -lt $hv.Bottom -or $hv.Top -lt $peer.Current.BoundingRectangle.Top -or
        $hv.Bottom -gt $peer.Current.BoundingRectangle.Bottom) { throw 'Selected header viewport is not fully bound.' }
    $searchValuePattern=$null
    if (-not $search.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$searchValuePattern)) { throw 'Cannot read member search value.' }
    $headerScrollState=@{inner_runtime_id=($header.GetRuntimeId() -join ',');
        viewport_runtime_id=($headerViewport.GetRuntimeId() -join ',');scroll_runtime_id=($headerScroll.GetRuntimeId() -join ',');
        search_runtime_id=($search.GetRuntimeId() -join ',');search_value=[string]$searchValuePattern.Current.Value;
        viewport=(RectOf $headerViewport);inner=(RectOf $header);offset=($hv.Top-$hr.Top);max_offset=($hr.Height-$hv.Height)}
    $id=$box.GetRuntimeId() -join ','
    $b=$box.Current.BoundingRectangle
    $left=[int][Math]::Floor($b.Left); $top=[int][Math]::Floor($b.Top)
    $width=[int][Math]::Ceiling($b.Right)-$left; $height=[int][Math]::Ceiling($b.Bottom)-$top
    $regions=@{list=(RectOf $list);viewport=(RectOf $viewport);header=(RectOf $headerViewport);search=(RectOf $search)}
    [DialogCapture]::Save([IntPtr]::new($handleValue),$left,$top,$width,$height,$path)
    $live=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($handleValue))
    $liveBox=Single @(FindClass $live 'class Ui::BoxLayerWidget') 'unchanged dialog'
    if ($live.Current.ProcessId -ne $expectedPid -or ($liveBox.GetRuntimeId() -join ',') -ne $id -or
        $liveBox.Current.BoundingRectangle -ne $b) { throw 'Window or dialog changed while capturing. Retry the read-only check.' }
    $ocr=@{available=$false;error='';language='';languages=@();text='';words=@()}
    $headerDiagnostic=$null
    $bitmap=$null; $stream=$null;$firstRowPath=$null;$ocrScanBounds=$null
    if ($payload.capture_only -ne $true) { try {
        $ocrPath=$path
        if ($payload.header_scan_only -eq $true) {
            $headerBottom=[Math]::Min($top+$height,$regions.header.top+$regions.header.height)
            $stripHeight=[int][Math]::Floor(($headerBottom-$top)*2)
            $firstRowPath=Join-Path ([IO.Path]::GetTempPath()) ('TGSelectedHeader-'+[Guid]::NewGuid().ToString('N')+'.png')
            [DialogCapture]::SaveCrop($path,$firstRowPath,($width*2),$stripHeight)
            $ocrPath=$firstRowPath;$ocrScanBounds=@(0,0,($width*2),$stripHeight)
        } elseif ($payload.first_row_scan -eq $true) {
            $unit=[double]$regions.search.height/25.0
            if ($unit -lt 0.4 -or $unit -gt 4 -or $regions.list.top -lt $regions.viewport.top-0.5) {
                throw 'Cannot confirm the first search row is visible.'
            }
            $firstBottom=[Math]::Min($top+$height,[Math]::Min(
                $regions.list.top+$regions.list.height,[Math]::Min(
                $regions.viewport.top+$regions.viewport.height,$regions.list.top+76*$unit)))
            $stripHeight=[int][Math]::Floor(($firstBottom-$top)*2)
            $firstRowPath=Join-Path ([IO.Path]::GetTempPath()) ('TGFirstRow-'+[Guid]::NewGuid().ToString('N')+'.png')
            [DialogCapture]::SaveCrop($path,$firstRowPath,($width*2),$stripHeight)
            $ocrPath=$firstRowPath;$ocrScanBounds=@(0,0,($width*2),$stripHeight)
        }
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
        [Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null
        [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
        [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
        [Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime] | Out-Null
        [Windows.Media.Ocr.OcrResult,Windows.Media.Ocr,ContentType=WindowsRuntime] | Out-Null
        $script:asTask=@([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
            $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        })[0]
        if ($null -eq $script:asTask) { throw 'Windows Runtime async bridge is unavailable.' }
        function AwaitResult($operation,[Type]$resultType) {
            $task=$script:asTask.MakeGenericMethod($resultType).Invoke($null,@($operation))
            if (-not $task.Wait(10000)) { throw 'OCR step timed out.' }
            return $task.GetAwaiter().GetResult()
        }
        $languages=@([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages)
        $ocr.languages=@($languages | ForEach-Object { $_.LanguageTag })
        $language=@($languages | Where-Object { $_.LanguageTag -like 'en-*' } | Select-Object -First 1)
        $engine=$null
        if ($language.Count -eq 1) { $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language[0]) }
        if ($null -eq $engine) { $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
        if ($null -eq $engine -and $languages.Count -gt 0) { $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($languages[0]) }
        if ($null -eq $engine) { throw 'No installed OCR language is available.' }
        $file=AwaitResult ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ocrPath)) ([Windows.Storage.StorageFile])
        $stream=AwaitResult ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder=AwaitResult ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap=AwaitResult ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        if ($bitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
            $bitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) { throw 'Capture exceeds OCR size limit.' }
        $result=AwaitResult ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $words=New-Object System.Collections.Generic.List[object]
        foreach ($line in $result.Lines) {
            foreach ($word in $line.Words) {
                $r=$word.BoundingRect
                $words.Add(@{text=$word.Text;left=$r.X;top=$r.Y;width=$r.Width;height=$r.Height})
            }
        }
        $ocr.available=$true; $ocr.language=$engine.RecognizerLanguage.LanguageTag
        $ocr.text=$result.Text; $ocr.words=@($words.ToArray())
        if ($payload.header_diagnostic -eq $true) {
            # Diagnostic evidence only. Never replace whole-dialog OCR or approve
            # selection from this crop; avatar characters may also be recognized.
            $headerPath=Join-Path ([IO.Path]::GetDirectoryName($path)) ([IO.Path]::GetFileNameWithoutExtension($path)+'_header.png')
            $headerDiagnostic=@{available=$false;error='';image_path=$headerPath;scale=4;
                text='';words=@();language=$engine.RecognizerLanguage.LanguageTag;
                used_for_selection=$false}
            try {
                $hr=$regions.header
                $hx=[int][Math]::Floor(($hr.left-$left)*2)
                $hy=[int][Math]::Floor(($hr.top-$top)*2)
                $hw=[int][Math]::Ceiling(($hr.left+$hr.width-$left)*2)-$hx
                $hh=[int][Math]::Ceiling(($hr.top+$hr.height-$top)*2)-$hy
                $headerDiagnostic.capture=@{left=$left+$hx/2.0;top=$top+$hy/2.0;width=$hw/2.0;height=$hh/2.0}
                [DialogCapture]::SaveHeader($path,$headerPath,$hx,$hy,$hw,$hh,[Windows.Media.Ocr.OcrEngine]::MaxImageDimension)
                $bitmap.Dispose();$bitmap=$null
                $stream.Dispose();$stream=$null
                $file=AwaitResult ([Windows.Storage.StorageFile]::GetFileFromPathAsync($headerPath)) ([Windows.Storage.StorageFile])
                $stream=AwaitResult ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
                $decoder=AwaitResult ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
                $bitmap=AwaitResult ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                    [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
                $headerResult=AwaitResult ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
                $headerWords=New-Object System.Collections.Generic.List[object]
                foreach ($line in $headerResult.Lines) {
                    foreach ($word in $line.Words) {
                        $wr=$word.BoundingRect
                        $headerWords.Add(@{text=$word.Text;left=$wr.X;top=$wr.Y;width=$wr.Width;height=$wr.Height})
                    }
                }
                $headerDiagnostic.available=$true;$headerDiagnostic.text=$headerResult.Text
                $headerDiagnostic.words=@($headerWords.ToArray())
            } catch { $headerDiagnostic.error=$_.Exception.Message }
        }
    } catch { $ocr.error=$_.Exception.Message }
    finally {
        if ($null -ne $bitmap) { try {$bitmap.Dispose()} catch {} }
        if ($null -ne $stream) { try {$stream.Dispose()} catch {} }
        if ($null -ne $firstRowPath) { Remove-Item -LiteralPath $firstRowPath -ErrorAction SilentlyContinue }
    } }
    @{ok=$true;read_only=$true;scope='member_visual';image_path=$path;scale=2;
        process_id=$expectedPid;window_handle=$handleValue;
        dialog_runtime_id=$id;button_regions=@{add=(RectOf $add[0]);cancel=(RectOf $cancel[0])};
        capture=@{left=$left;top=$top;width=$width;height=$height};regions=$regions;ocr=$ocr;
        header_diagnostic=$headerDiagnostic;header_scroll=$headerScrollState;
        first_row_scan=($payload.first_row_scan -eq $true);header_scan_only=($payload.header_scan_only -eq $true);ocr_scan_bounds=$ocrScanBounds;
        final_invite_clicked=$false} | ConvertTo-Json -Depth 8 -Compress
} catch {
    @{ok=$false;error=$_.Exception.Message;final_invite_clicked=$false} | ConvertTo-Json -Compress
    return
}
