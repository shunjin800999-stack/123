$ErrorActionPreference='Stop'
$s=Get-Content (Join-Path $PSScriptRoot 'navigate_group.ps1') -Raw -Encoding UTF8
$a=$s.IndexOf('    function WaitMemberButton {');$b=$s.IndexOf('    $memberWait=', $a)
Invoke-Expression $s.Substring($a,$b-$a)
function VerifyProfile {
 $script:reads++
 if ($script:mode -eq 'wrong') { throw 'Wrong group profile' }
 if ($script:mode -eq 'ready' -or ($script:mode -eq 'delayed' -and $script:reads -ge 3)) { return 'verified button' }
 return $null
}
$script:mode='ready';$script:reads=0
if ((WaitMemberButton) -ne 'verified button' -or $script:reads -ne 1) { throw 'Ready entry did not return immediately' }
$script:mode='delayed';$script:reads=0
if ((WaitMemberButton) -ne 'verified button' -or $script:reads -ne 3) { throw 'Delayed entry not reread' }
$script:mode='wrong';$script:reads=0;$failed=$false
try { WaitMemberButton } catch { $failed=$true }
if (-not $failed -or $script:reads -ne 1) { throw 'Wrong identity was retried' }
$script:mode='missing';$script:reads=0;$failed=$false;$clock=[Diagnostics.Stopwatch]::StartNew()
try { WaitMemberButton } catch { $failed=$true }
if (-not $failed -or $clock.ElapsedMilliseconds -lt 2500 -or $clock.ElapsedMilliseconds -gt 4000) { throw 'Missing entry did not stop at bounded deadline' }
$errors=$null;$tokens=$null
[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'navigate_group.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw $errors[0].Message }
'4 readiness scenarios and production PowerShell parse passed.'
