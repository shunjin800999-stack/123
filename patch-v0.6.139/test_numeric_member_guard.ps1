param([string]$GlyphCasesPath='')
# Execute the production crop/check code and its actual C# pixel algorithms.
# Memory bitmaps replace GDI only; no UIA, SendInput or actual window is used.
$ErrorActionPreference='Stop'
$source=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'select_member.ps1') -Raw -Encoding UTF8
$start=$source.IndexOf('    public static void SameRow(')
$end=$source.IndexOf('    public static void SameHeader(',$start)
if ($start -lt 0 -or $end -le $start) { throw 'Production pixel guards missing' }
$methods=$source.Substring($start,$end-$start)
$support=@'
using System;
using System.Collections.Generic;
using Bitmap = GuardTests.MemoryBitmap;
namespace GuardTests {
    public struct Pixel {
        public int value;
        public int R { get { return (value>>16)&255; } }
        public int G { get { return (value>>8)&255; } }
        public int B { get { return value&255; } }
        public int ToArgb() { return value; }
    }
    public struct PixelSize {
        public int width,height;
        public static bool operator ==(PixelSize a,PixelSize b) { return a.width==b.width&&a.height==b.height; }
        public static bool operator !=(PixelSize a,PixelSize b) { return !(a==b); }
        public override bool Equals(object obj) { return obj is PixelSize&&this==(PixelSize)obj; }
        public override int GetHashCode() { return width^height; }
    }
    public class Raster {
        public int width=728,height=1160;
        public Dictionary<int,int> pixels=new Dictionary<int,int>();
    }
    public class MemoryBitmap : IDisposable {
        public static Dictionary<string,Raster> images=new Dictionary<string,Raster>();
        public Raster raster;
        public MemoryBitmap(string path) { raster=images[path]; }
        public int Width { get { return raster.width; } }
        public int Height { get { return raster.height; } }
        public PixelSize Size { get { return new PixelSize{width=Width,height=Height}; } }
        public Pixel GetPixel(int x,int y) {
            if(x<0||y<0||x>=Width||y>=Height) throw new Exception("Pixel outside bitmap");
            int value;return new Pixel{value=raster.pixels.TryGetValue(y*Width+x,out value)?value:0xffffff};
        }
        public void Dispose() {}
        public static void Reset() { images.Clear();images["before"]=new Raster();images["current"]=new Raster(); }
        public static void Both(int x,int y,int value) { images["before"].pixels[y*728+x]=value;images["current"].pixels[y*728+x]=value; }
        public static void Current(int x,int y,int value) { images["current"].pixels[y*728+x]=value; }
    }
}
public static class MemberInput {
'@
Add-Type -TypeDefinition ($support+$methods+"`n}")
$pointStart=$source.IndexOf("    `$guardStage='point'")
$pointEnd=$source.IndexOf('    if ($payload.target_member_only -ne $true)',$pointStart)
if ($pointStart -lt 0 -or $pointEnd -le $pointStart) { throw 'Production crop block missing' }
$block=$source.Substring($pointStart,$pointEnd-$pointStart)
$script:count=0
function Run-GuardCase([string]$name,[bool]$expected,[bool]$target=$true,[string]$change='',
        [bool]$merged=$false,[bool]$heightOnly=$false,[string]$number='2429',$glyph=$null) {
    [GuardTests.MemoryBitmap]::Reset()
    $width=14*$number.Length;$height=19;$inkTop=$(if ($number -eq '2416') {231} else {351})
    if ($glyph) {
        $width=[int]$glyph.width;$height=[int]$glyph.height
        foreach ($pixel in $glyph.pixels) {
            [GuardTests.MemoryBitmap]::Both(148+[int]$pixel[0],$inkTop+[int]$pixel[1],[int]$pixel[2])
        }
    } else {
        for ($i=0;$i -lt $number.Length;$i++) {
            for ($dx=0;$dx -lt 11;$dx++) {
                for ($dy=0;$dy -lt 19;$dy++) { [GuardTests.MemoryBitmap]::Both(148+14*$i+$dx,$inkTop+$dy,0x202020) }
            }
        }
    }
    # Put the animated suffix in the old full-row comparison band, also
    # inside a merged number+emoji OCR box when requested.
    $emojiX=148+$width+6
    for ($dx=0;$dx -lt 15;$dx++) {
        for ($dy=0;$dy -lt 18;$dy++) { [GuardTests.MemoryBitmap]::Both($emojiX+$dx,$inkTop+$dy,0x805000) }
    }
    $nameWidth=$width+$(if ($merged) { 24 } else { 0 })
    $payload=[pscustomobject]@{number=$number;target_member_only=$target;
        numeric_text=$number;numeric_source='windows_numeric';
        numeric_bounds=[pscustomobject]@{left=822.0;top=218+$inkTop/2.0;width=$width/2.0;height=$height/2.0};
        name_bounds=[pscustomobject]@{left=822.0;top=218+$inkTop/2.0;width=$nameWidth/2.0;height=$height/2.0};x=0;y=0}
    $payload.x=[int][Math]::Floor($payload.name_bounds.left+$payload.name_bounds.width/2)
    $payload.y=[int][Math]::Floor($payload.name_bounds.top+$payload.name_bounds.height/2)
    $fresh=[pscustomobject]@{capture=[pscustomobject]@{left=748;top=218;width=364;height=580};
        regions=[pscustomobject]@{list=[pscustomobject]@{left=748;top=370;width=364;height=76};
            viewport=[pscustomobject]@{left=748;top=370;width=364;height=374};
            header=[pscustomobject]@{left=748;top=266;width=364;height=104};
            search=[pscustomobject]@{left=844;top=339;width=218;height=25}};
        button_regions=[pscustomobject]@{add=[pscustomobject]@{left=1045;top=754;width=57;height=34};
            cancel=[pscustomobject]@{left=964;top=754;width=75;height=34}}}
    if ($number -eq '2416') {
        $fresh.regions.list.top=310;$fresh.regions.viewport.top=310;$fresh.regions.header.height=44
        $fresh.regions.search.left=794;$fresh.regions.search.top=279;$fresh.regions.search.width=268
    }
    if ($change -in @('joined_ink','joined_changed')) {
        for ($dx=0;$dx -lt $width;$dx++) { [GuardTests.MemoryBitmap]::Both(148+$dx,$inkTop+9,0x202020) }
        if ($change -eq 'joined_changed') { [GuardTests.MemoryBitmap]::Current(148+$width-2,$inkTop+9,0xffffff) }
    }
    switch ($change) {
        'emoji' { [GuardTests.MemoryBitmap]::Current($emojiX+2,$inkTop+6,0x305080) }
        'avatar' { [GuardTests.MemoryBitmap]::Current(40,$inkTop+6,0x222222) }
        'both' { [GuardTests.MemoryBitmap]::Current($emojiX+2,$inkTop+6,0x305080);[GuardTests.MemoryBitmap]::Current(40,$inkTop+6,0x222222) }
        'digit' {
            $first=[GuardTests.MemoryBitmap]::images['before'].pixels.Keys | Where-Object { ($_ % 728) -lt (148+$width) } | Select-Object -First 1
            [GuardTests.MemoryBitmap]::images['current'].pixels[$first]=0xffffff
        }
        'last_digit' { [GuardTests.MemoryBitmap]::Current(148+14*($number.Length-1)+5,$inkTop+6,0xffffff) }
        'moved_digits' {
            [GuardTests.MemoryBitmap]::images['current'].pixels.Clear()
            foreach ($entry in [GuardTests.MemoryBitmap]::images['before'].pixels.GetEnumerator()) {
                [GuardTests.MemoryBitmap]::images['current'].pixels[$entry.Key+2]=$entry.Value
            }
        }
        'dimensions' { [GuardTests.MemoryBitmap]::images['current'].width=730 }
        'bad_point' { $payload.x=0 }
        'outside_list' { $payload.name_bounds.left=730;$payload.x=744 }
        'empty_bounds' { $payload.name_bounds.height=0 }
        'gray_ink' {
            foreach ($key in @([GuardTests.MemoryBitmap]::images['before'].pixels.Keys)) {
                if (($key % 728) -lt (148+$width)) {
                    [GuardTests.MemoryBitmap]::images['before'].pixels[$key]=0xd2d2d2
                    [GuardTests.MemoryBitmap]::images['current'].pixels[$key]=0xd2d2d2
                }
            }
        }
        'missing_numeric' { $payload.numeric_bounds=$null }
        'empty_numeric' { $payload.numeric_bounds.width=0 }
        'wrong_numeric_text' { $payload.numeric_text='999' }
        'wrong_source' { $payload.numeric_source='ink_groups' }
        'other_row' { $payload.numeric_bounds.top+=56 }
        'outside_numeric' { $payload.numeric_bounds.width=500 }
        'nan_numeric' { $payload.numeric_bounds.left=[double]::NaN }
    }
    $beforePath='before';$guardPath='current';$listHeightOnly=$heightOnly;$rowComparison=$null;$guardStage=''
    if ($heightOnly) { $fresh.regions.list.height=244 }
    $passed=$true;$errorText=''
    try { Invoke-Expression $block } catch { $passed=$false;$errorText=$_.Exception.GetBaseException().Message }
    if ($passed -ne $expected) { throw "$name expected=$expected actual=$passed error=$errorText" }
    if ($passed -and $target) {
        $crop=$rowComparison.pixel_bounds
        if ($rowComparison.scope -ne 'ocr_numeric' -or $crop[0] -ne 148 -or $crop[1] -ne $inkTop -or
                ($crop[0]+$crop[2]) -gt (148+$width) -or $crop[2] -ge 728 -or $crop[3] -ne $height) {
            throw "$name did not exclude the animated suffix/avatar"
        }
    }
    if (-not $passed -and $heightOnly -and $change -eq 'last_digit' -and $guardStage -ne 'layout') {
        throw 'Changed numeric name no longer returns to the existing pre-input refresh'
    }
    $script:count++
}
Run-GuardCase '2429 normal' $true
Run-GuardCase '2429 animated emoji' $true -change emoji
Run-GuardCase '2429 avatar' $true -change avatar
Run-GuardCase '2429 both' $true -change both
Run-GuardCase '2429 merged emoji box' $true -change emoji -merged $true
Run-GuardCase '2429 merged box and avatar' $true -change both -merged $true
Run-GuardCase 'legacy emoji check unchanged' $false -target $false -change emoji
Run-GuardCase 'legacy avatar check unchanged' $false -target $false -change avatar
Run-GuardCase 'legacy normal unchanged' $true -target $false
Run-GuardCase 'numeric glyph changed' $false -change digit
Run-GuardCase 'last numeric glyph changed' $false -change last_digit
Run-GuardCase 'digits moved' $false -change moved_digits
Run-GuardCase 'capture size changed' $false -change dimensions
Run-GuardCase 'wrong click point' $false -change bad_point
Run-GuardCase 'name outside list' $false -change outside_list
Run-GuardCase 'empty name box' $false -change empty_bounds
Run-GuardCase 'low contrast digits need no ink threshold' $true -change gray_ink
Run-GuardCase 'loading list with animated suffix' $true -change emoji -heightOnly $true
Run-GuardCase 'loading list with changed number' $false -change last_digit -heightOnly $true
foreach ($number in @('0','1','12','001','1000','123456')) {
    Run-GuardCase "numeric $number animated suffix" $true -number $number -merged $true -change emoji
}
Run-GuardCase '2416 original tight OCR geometry' $true -number 2416
Run-GuardCase '2416 joined digit bands' $true -number 2416 -change joined_ink
Run-GuardCase '2416 joined bands with changed digit' $false -number 2416 -change joined_changed
foreach ($change in @('missing_numeric','empty_numeric','wrong_numeric_text','wrong_source','other_row','outside_numeric','nan_numeric')) {
    Run-GuardCase "invalid numeric OCR $change" $false -change $change
}
if ($GlyphCasesPath) {
    foreach ($glyph in (Get-Content -LiteralPath $GlyphCasesPath -Raw -Encoding UTF8 | ConvertFrom-Json)) {
        Run-GuardCase $glyph.name $true -number $glyph.number -glyph $glyph -merged $true -change both
        Run-GuardCase ($glyph.name+' changed digit') $false -number $glyph.number -glyph $glyph -merged $true -change digit
    }
}
Write-Output "$script:count production numeric pixel-guard scenarios passed; animated suffix/avatar excluded, changed digits blocked. No Windows input sent."
