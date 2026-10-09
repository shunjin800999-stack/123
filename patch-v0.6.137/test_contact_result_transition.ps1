# Exercise the production state classifier and bounded wait without Windows UI input.
$ErrorActionPreference='Stop'
$source=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'submit_contact.ps1') -Raw -Encoding UTF8
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw $errors[0].Message }
foreach ($name in @('GetContactResultState','RecordContactResultReadError','WaitContactResultPage')) {
    $fn=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true))
    if ($fn.Count -ne 1) { throw "Missing production helper: $name" }
    Invoke-Expression $fn[0].Extent.Text
}
$classifications=@(
    @{args=@(0,0,0,1,$true,$false,$false);expected='chat'},
    @{args=@(0,0,1,1,$true,$false,$false);expected='profile'},
    @{args=@(1,0,0,1,$true,$false,$false);expected='transition'},
    @{args=@(1,0,1,1,$true,$false,$false);expected='transition'},
    @{args=@(1,1,0,1,$true,$false,$false);expected='transition'},
    @{args=@(1,1,0,0,$false,$true,$false);expected='result_dialog'},
    @{args=@(1,0,0,0,$false,$false,$true);expected='result_dialog'},
    @{args=@(2,0,0,1,$true,$false,$true);expected='transition'},
    @{args=@(0,0,2,1,$true,$false,$false);expected='transition'},
    @{args=@(0,0,1,1,$false,$false,$false);expected='transition'},
    @{args=@(0,0,0,1,$false,$false,$false);expected='transition'},
    @{args=@(0,0,0,0,$true,$false,$false);expected='transition'}
)
foreach ($case in $classifications) {
    $args=$case.args
    if ((GetContactResultState @args) -ne $case.expected) { throw 'Incorrect page classification' }
}
function AssertRoot {
    $script:checks++
    if ($script:wrongRoot) { throw 'The filled form window changed.' }
}
function Start-Sleep { param([int]$Milliseconds)
    $resultClock.ElapsedMilliseconds+=$Milliseconds;$script:frame++
}
function ReadContactResultPage {
    $script:reads++
    $state=$script:frames[[Math]::Min($script:frame,$script:frames.Count-1)]
    if ($state -eq 'read_error') { throw 'Provider changed during animation' }
    return [PSCustomObject]@{state=$state;summary=[ordered]@{state=$state;profile_count=$(if ($state -eq 'profile') {1} else {0})}}
}
function RunWaitCase($frames,$allowChat,$expected,$expectedReads,$expectedMs,$fail=$false,$wrongRoot=$false,$limitMs=10000) {
    $script:frames=$frames;$script:frame=0;$script:reads=0;$script:checks=0;$script:wrongRoot=$wrongRoot
    $script:resultClock=[PSCustomObject]@{ElapsedMilliseconds=0}
    $script:report=@{result_read_errors=@();result_page_history=@();result_page_reads=0}
    $failed=$false;$page=$null
    try { $page=WaitContactResultPage $allowChat $limitMs } catch { $failed=$true }
    if ($failed -ne $fail -or $script:reads -ne $expectedReads -or $resultClock.ElapsedMilliseconds -ne $expectedMs) {
        throw "Unexpected wait outcome for $frames : failure=$failed reads=$script:reads ms=$($resultClock.ElapsedMilliseconds)"
    }
    if (-not $fail -and $page.state -ne $expected) { throw 'Wrong ready page' }
    if ($wrongRoot -and $script:reads -ne 0) { throw 'Changed window was read as a recoverable transition' }
    if ($report.result_page_history.Count -gt 12 -or $report.result_read_errors.Count -gt 8) { throw 'Unbounded diagnostics' }
}
RunWaitCase @('chat') $true 'chat' 1 0
RunWaitCase @('profile') $true 'profile' 1 0
RunWaitCase @('transition','transition','chat') $true 'chat' 3 400
RunWaitCase @('transition','profile') $true 'profile' 2 200
RunWaitCase @('result_dialog') $true 'result_dialog' 1 0
RunWaitCase @('chat','transition','profile') $false 'profile' 3 400
RunWaitCase @('result_dialog','transition','profile') $false 'profile' 3 400
RunWaitCase @('read_error','profile') $true 'profile' 2 200
if ($report.result_read_errors.Count -ne 1) { throw 'Provider failure was not recorded' }
RunWaitCase @('transition') $true '' 50 10000 $true
if ($report.last_result_page.state -ne 'transition') { throw 'Unresolved page was not recorded' }
RunWaitCase @('result_dialog') $false '' 50 10000 $true
RunWaitCase @('read_error') $true '' 50 10000 $true
RunWaitCase @('profile') $true '' 0 0 $true $true
RunWaitCase @('transition') $false '' 20 4000 $true $false 4000
Write-Output '12 page classifications and 13 production wait scenarios passed; ready pages have no fixed delay.'

# Run the actual post-Create orchestration with mock UIA providers. Create itself
# is outside this block; the existing queue tests cover its single persisted attempt.
Add-Type -TypeDefinition @'
namespace System.Windows.Automation {
    public enum TreeScope { Descendants }
    public enum ControlType { Button }
    public class AutomationElement { public static object ControlTypeProperty = new object(); }
    public class PropertyCondition { public PropertyCondition(object a, object b) {} }
    public class InvokePattern {
        public static object Pattern = new object();
        public static int Count;
        public void Invoke() { Count++; }
    }
}
'@
$a=$source.IndexOf('    # Read/navigation retries never repeat Create.')
$b=$source.IndexOf('    # Navigation never implies success;', $a)
if ($a -lt 0 -or $b -lt 0) { throw 'Production result orchestration missing' }
$orchestration=$source.Substring($a,$b-$a)
function AssertRoot { }
function AssertProcess { }
function Start-Sleep { param([int]$Milliseconds) }
function Visible($element) { return $true }
function Single($elements,$description) {
    $items=@($elements);if ($items.Count -ne 1) { throw "Expected exactly one $description" };return $items[0]
}
$script:infoPattern=New-Object System.Windows.Automation.InvokePattern
$script:info=[PSCustomObject]@{Current=[PSCustomObject]@{IsEnabled=$true;ClassName='class Ui::IconButton';Name='Info'}}
$script:info | Add-Member ScriptMethod TryGetCurrentPattern {
    param($id,$pattern) $pattern.Value=$script:infoPattern;return $true
}
$script:bar=[PSCustomObject]@{}
$script:bar | Add-Member ScriptMethod GetRuntimeId { return 'bar-1' }
$script:bar | Add-Member ScriptMethod FindAll { param($scope,$condition) return @($script:info) }
$script:profile=[PSCustomObject]@{}
$script:profile | Add-Member ScriptMethod GetRuntimeId { return 'profile-1' }
function FindClass($root,$class) {
    if ($class -eq 'class HistoryView::TopBarWidget') { return @($script:bar) }
    return @()
}
function ReadContactResultPage {
    $state=$script:frames[[Math]::Min($script:frame,$script:frames.Count-1)]
    $script:frame++
    return [PSCustomObject]@{state=$state;root='root';profiles=@($script:profile);
        summary=[ordered]@{state=$state;profile_count=$(if ($state -eq 'profile') {1} else {0})}}
}
function RunNavigationCase($frames,$infoClicks,$alreadyOpen) {
    $script:frames=$frames;$script:frame=0;[System.Windows.Automation.InvokePattern]::Count=0
    $number='1215'
    $report=[ordered]@{create_invoked=$true;profile_open_attempted=$false;profile_open_invoked=$false;
        profile_already_open=$false;profile_opened=$false;result_page_verified=$false}
    Invoke-Expression $orchestration
    if ([System.Windows.Automation.InvokePattern]::Count -ne $infoClicks -or $report.profile_already_open -ne $alreadyOpen) {
        throw 'Info was repeated or an existing profile was reopened'
    }
    if (-not $report.ok -or -not $report.result_page_verified -or $report.profile_runtime_id -ne 'profile-1' -or
            $report.state -ne 'profile_opened' -or -not $report.create_invoked) { throw 'Missing navigation proof' }
}
RunNavigationCase @('profile') 0 $true
RunNavigationCase @('chat','profile','profile') 0 $true
RunNavigationCase @('chat','transition','chat','chat','profile') 1 $false
RunNavigationCase @('chat','chat','result_dialog','transition','profile') 1 $false
Write-Output '4 production navigation scenarios passed; an existing profile is reused and Info is invoked at most once.'
