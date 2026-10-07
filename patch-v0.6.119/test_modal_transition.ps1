# Deterministic tests of production wait logic; no Telegram or UI input.
$ErrorActionPreference='Stop'
$source=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'username_contact.ps1') -Raw -Encoding UTF8
$start=$source.IndexOf(' function WaitSubmittedFormExit {')
$end=$source.IndexOf(' function ReopenSubmittedProfile(', $start)
if ($start -lt 0 -or $end -lt 0) { throw 'Production helper missing' }
Invoke-Expression $source.Substring($start,$end-$start)
function RootCheck { $script:checks++ }
function Start-Sleep { param([int]$Milliseconds) $actionClock.ElapsedMilliseconds+=$Milliseconds;$script:frame++ }
function Classes($element,$class) {
 $state=$script:frames[[Math]::Min($script:frame,$script:frames.Count-1)]
 if ($class -eq 'class Ui::BoxLayerWidget') {
  if ($state -eq 'absent') { return @() }
  if ($state -eq 'multiple') { return @('layer1','layer2') }
  return @('layer1')
 }
 if ($state -eq 'empty') { return @() }
 $node=[PSCustomObject]@{id= $(if ($state -eq 'original') { 'original' } else { 'different' })}
 $node | Add-Member ScriptMethod GetRuntimeId { return $this.id }
 return @($node)
}
function RunCase($frames,$fail,$changed) {
 $script:frames=$frames;$script:frame=0;$script:checks=0
 $script:actionClock=[PSCustomObject]@{ElapsedMilliseconds=0}
 $script:r=@{};$script:p=@{contact_runtime_id='original'};$script:root='root'
 $failed=$false
 try { WaitSubmittedFormExit } catch { $failed=$true }
 if ($failed -ne $fail) { throw 'Unexpected outcome' }
 if ($fail) {
  if ($r.contact_form_absent -or $r.submit_form_preserved_until_exit) { throw 'Unresolved modal accepted' }
  if ($actionClock.ElapsedMilliseconds -ne 2000) { throw 'Deadline not bounded' }
 } else {
  if (-not $r.contact_form_absent -or -not $r.submit_form_preserved_until_exit) { throw 'Missing exit proof' }
  if ([bool]$r.modal_transition_resolved -ne $changed) { throw 'Missing transition evidence' }
 }
}
RunCase @('absent') $false $false
RunCase @('original','absent') $false $false
RunCase @('original','empty','different','absent') $false $true
RunCase @('multiple','absent') $false $true
RunCase @('different') $true $true
RunCase @('original') $true $false
Write-Output '6 modal-transition scenarios passed; persistent dialogs never accepted.'
