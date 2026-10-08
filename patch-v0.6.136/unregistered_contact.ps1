# Dismiss only the complete, twice verified unregistered AddContactBox result.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$r=[ordered]@{ok=$false;scope='unregistered_contact';process_path_verified=$false;signature_verified=$false;dismiss_attempted=$false;dismiss_invoked=$false;result_absent=$false;create_attempted=$false;final_invite_clicked=$false;contact_database_updated=$false}
try {
 Add-Type -AssemblyName UIAutomationClient
 Add-Type -AssemblyName UIAutomationTypes
 $p=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
 $h=[Int64]::Parse($env:TG_INSPECT_HWND);$pidExpected=[Int32]::Parse($env:TG_INSPECT_PID)
 $rootId=[string]$p.main_runtime_id;$dialogId=[string]$p.dialog_runtime_id
 if ([string]::IsNullOrWhiteSpace($rootId) -or [string]::IsNullOrWhiteSpace($dialogId) -or [string]$p.number -notmatch '^(0|[1-9][0-9]{0,5})$') { throw 'Missing bound result identity.' }
 $path=[IO.Path]::GetFullPath([string]$p.executable_path)
 $r.window_handle=$h;$r.process_id=$pidExpected;$r.main_runtime_id=$rootId;$r.dialog_runtime_id=$dialogId;$r.number=[string]$p.number
 function Visible($e) { $c=$e.Current;$b=$c.BoundingRectangle;return ($c.ProcessId -eq $pidExpected -and -not $c.IsOffscreen -and $b.Width -gt 0 -and $b.Height -gt 0) }
 function Root {
  $proc=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = '+$pidExpected)
  if ($null -eq $proc -or -not [string]::Equals([IO.Path]::GetFullPath($proc.ExecutablePath),$path,[StringComparison]::OrdinalIgnoreCase)) { throw 'Executable changed.' }
  $r.process_path_verified=$true
  $e=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($h))
  if ($null -eq $e -or -not (Visible $e) -or $e.Current.ClassName -ne 'class MainWindow' -or ($e.GetRuntimeId() -join ',') -ne $rootId) { throw 'Window changed.' }
  return $e
 }
 function Boxes($root) { return @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,'class Ui::BoxLayerWidget')) | Where-Object { Visible $_ }) }
 function MatchResult {
  $root=Root;$boxes=@(Boxes $root)
  if ($boxes.Count -ne 1 -or ($boxes[0].GetRuntimeId() -join ',') -ne $dialogId) { throw 'Result dialog changed.' }
  $rows=@($boxes[0].FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object { Visible $_ })
  $contacts=@($rows | Where-Object { $_.Current.ClassName -eq 'class AddContactBox' -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Window })
  $titles=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and $_.Current.Name -in @('New Contact','Novo Contato') })
  $buttons=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button })
  $edits=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit })
  if ($contacts.Count -ne 1 -or $titles.Count -ne 1 -or $edits.Count -ne 0 -or $buttons.Count -ne 1 -or -not $buttons[0].Current.IsEnabled -or $buttons[0].Current.Name -notin @('Try someone else','Tentar outro') -or $buttons[0].Current.ClassName -ne 'class Ui::RoundButton') { throw 'Unregistered result signature changed.' }
  return $buttons[0]
 }
 $button=MatchResult;$buttonId=$button.GetRuntimeId() -join ','
 Start-Sleep -Milliseconds 200
 $button=MatchResult
 if (($button.GetRuntimeId() -join ',') -ne $buttonId) { throw 'Dismiss button changed.' }
 $pattern=$null
 if (-not $button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern) -or $pattern -isnot [System.Windows.Automation.InvokePattern]) { throw 'No supported InvokePattern.' }
 $r.signature_verified=$true;$r.dismiss_attempted=$true
 ([System.Windows.Automation.InvokePattern]$pattern).Invoke();$r.dismiss_invoked=$true
 for ($i=0;$i -lt 15;$i++) {
  Start-Sleep -Milliseconds 200;$root=Root
  $buttons=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)) | Where-Object { (Visible $_) -and $_.Current.Name -in @('Try someone else','Tentar outro') })
  if ($buttons.Count -eq 0) { $r.result_absent=$true;break }
 }
 if (-not $r.result_absent) { throw 'Result did not clear; no retry.' }
 $r.ok=$true;$r | ConvertTo-Json -Depth 6 -Compress
} catch { $r.error=$_.Exception.Message;$r | ConvertTo-Json -Depth 6 -Compress;exit 1 }
