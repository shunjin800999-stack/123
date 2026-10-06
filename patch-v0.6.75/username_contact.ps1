# Username contact actions: bind the profile and modal before any input.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$r=[ordered]@{ok=$false;scope='username_contact';stage='initial';state='review';process_path_verified=$false;default_instance_route_verified=$false;username_verified=$false;field_schema_verified=$false;fields_verified=$false;fields_written=$false;add_invoked=$false;create_attempted=$false;create_invoked=$false;profile_verified=$false;final_invite_clicked=$false;contact_database_updated=$false;errors=@()}
try {
 Add-Type -AssemblyName UIAutomationClient
 Add-Type -AssemblyName UIAutomationTypes
 $p=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
 $mode=[string]$p.mode;$username=([string]$p.username).ToLowerInvariant();$number=[string]$p.number
 if ($mode -notin @('open','fill','submit','dismiss_missing','close_saved') -or $username -notmatch '^@[a-z0-9_]{1,32}$') { throw 'Invalid username action.' }
 if ($mode -in @('fill','submit','close_saved') -and $number -notmatch '^[1-9][0-9]{0,5}$') { throw 'Invalid contact number.' }
 $h=[Int64]::Parse($env:TG_INSPECT_HWND);$expectedPid=[Int32]::Parse($env:TG_INSPECT_PID)
 $path=[IO.Path]::GetFullPath([string]$p.executable_path)
 $r.mode=$mode;$r.username=$username;$r.number=$number;$r.window_handle=$h;$r.process_id=$expectedPid;$r.executable_path=$path
 $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($h))
 if ($null -eq $root -or $root.Current.ProcessId -ne $expectedPid -or $root.Current.ClassName -ne 'class MainWindow') { throw 'Bound Telegram window changed.' }
 $rootId=$root.GetRuntimeId() -join ',';$r.main_runtime_id=$rootId
 if ($mode -in @('fill','submit','close_saved') -and ($rootId -ne [string]$p.main_runtime_id -or [string]::IsNullOrWhiteSpace([string]$p.profile_runtime_id))) { throw 'Original profile/window identity missing.' }
 function Visible($e) { $c=$e.Current;$b=$c.BoundingRectangle;return ($c.ProcessId -eq $expectedPid -and -not $c.IsOffscreen -and $b.Width -gt 0 -and $b.Height -gt 0) }
 function Single($elements,[string]$label) { $a=@($elements);if ($a.Count -ne 1) { throw ('Expected one '+$label+'.') };return $a[0] }
 function Rows($e) {
  for ($attempt=0;$attempt -lt 4;$attempt++) {
   try { return @($e.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object { Visible $_ }) }
   catch { if ($attempt -eq 3) { throw };Start-Sleep -Milliseconds 150;RootCheck }
  }
 }
 function Classes($e,[string]$name) {
  for ($attempt=0;$attempt -lt 4;$attempt++) {
   try { return @($e.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,$name)) | Where-Object { Visible $_ }) }
   catch { if ($attempt -eq 3) { throw };Start-Sleep -Milliseconds 150;RootCheck }
  }
 }
 function RootCheck {
  $proc=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = '+$expectedPid)
  if ($null -eq $proc -or -not [string]::Equals([IO.Path]::GetFullPath($proc.ExecutablePath),$path,[StringComparison]::OrdinalIgnoreCase)) { throw 'Executable changed.' }
  $now=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($h))
  if ($null -eq $now -or ($now.GetRuntimeId() -join ',') -ne $rootId -or -not (Visible $now)) { throw 'Window changed.' }
  $r.process_path_verified=$true
 }
 function Invoke($e) {
  RootCheck;$pattern=$null
  if (-not $e.Current.IsEnabled -or -not $e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern) -or $pattern -isnot [System.Windows.Automation.InvokePattern]) { throw 'Supported InvokePattern unavailable.' }
  ([System.Windows.Automation.InvokePattern]$pattern).Invoke()
 }
 function ClickDone {
  # InvokePattern can report success without saving this Qt form. Use a bound
  # physical click, after foreground, field and hit-target verification.
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class TelegramContactMouse {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [StructLayout(LayoutKind.Sequential)] public struct NativePoint { public int X; public int Y; }
 [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(NativePoint p);
 public static IntPtr WindowAt(int x, int y) { return WindowFromPoint(new NativePoint { X=x, Y=y }); }
 [DllImport("user32.dll")] public static extern bool GetCursorPos(out NativePoint point);
 [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint x, uint y, uint data, UIntPtr extra);
}
'@
  $dpiContext=[TelegramContactMouse]::SetThreadDpiAwarenessContext([IntPtr]::new(-4))
  if ($dpiContext -eq [IntPtr]::Zero) { throw 'Physical pixel DPI context unavailable; not clicked.' }
  $r.coordinate_space='physical_pixels'
  RootCheck
  [void][TelegramContactMouse]::SetForegroundWindow([IntPtr]::new($h))
  Start-Sleep -Milliseconds 200
  if ([TelegramContactMouse]::GetForegroundWindow() -ne [IntPtr]::new($h)) { throw 'Telegram is not foreground; Done not clicked.' }
  # Move focus out of the edited first-name field before final verification.
  $script:last.SetFocus();Start-Sleep -Milliseconds 150
  Filled
  $button=$script:done;$id=$button.GetRuntimeId() -join ','
  try { $point=$button.GetClickablePoint();$r.click_point_source='automation' }
  catch {
   $bounds=$button.Current.BoundingRectangle
   if ($bounds.Width -le 0 -or $bounds.Height -le 0) { throw 'Done has no visible bounds; not clicked.' }
   $point=[System.Windows.Point]::new(($bounds.Left+$bounds.Width/2),($bounds.Top+$bounds.Height/2))
   $r.click_point_source='verified_button_bounds'
  }
  if (-not $button.Current.IsEnabled -or -not (Visible $button)) { throw 'Done not available.' }
  if (-not [TelegramContactMouse]::SetCursorPos([int]$point.X,[int]$point.Y)) { throw 'Could not position mouse on verified Done.' }
  Start-Sleep -Milliseconds 100
  RootCheck;Filled
  if (($script:done.GetRuntimeId() -join ',') -ne $id) { throw 'Original Done button changed; not clicked.' }
  $bounds=$script:done.Current.BoundingRectangle
  $x=[int]$point.X;$y=[int]$point.Y
  $r.done_click_point=@{x=$x;y=$y}
  $r.done_bounds=@{left=$bounds.Left;top=$bounds.Top;width=$bounds.Width;height=$bounds.Height}
  if ($x -le $bounds.Left -or $x -ge $bounds.Right -or $y -le $bounds.Top -or $y -ge $bounds.Bottom) { throw 'Done click point outside current button; not clicked.' }
  $native=[TelegramContactMouse]::WindowAt($x,$y)
  $nativeRoot=[TelegramContactMouse]::GetAncestor($native,2)
  [uint32]$nativePid=0
  [void][TelegramContactMouse]::GetWindowThreadProcessId($native,[ref]$nativePid)
  $r.hit_window_handle=$native.ToInt64();$r.hit_root_handle=$nativeRoot.ToInt64();$r.hit_process_id=$nativePid
  if ($native -eq [IntPtr]::Zero -or $nativeRoot -ne [IntPtr]::new($h) -or $nativePid -ne $expectedPid -or [TelegramContactMouse]::GetForegroundWindow() -ne [IntPtr]::new($h)) { throw 'Done physical window target changed; not clicked.' }
  # Qt may return the enclosing accessibility widget at a valid button point.
  # The native hit window, exact bound form, unique Done and current bounds
  # determine the target; retain UIA hit details for diagnosis.
  try {
   $hit=[System.Windows.Automation.AutomationElement]::FromPoint($point)
   if ($null -ne $hit) {
    $r.uia_hit_runtime_id=$hit.GetRuntimeId() -join ',';$r.uia_hit_class=$hit.Current.ClassName
    $r.uia_hit_name=$hit.Current.Name;$r.uia_hit_process_id=$hit.Current.ProcessId
    if ($hit.Current.ProcessId -ne $expectedPid) { throw 'Done accessibility hit belongs to another process.' }
   }
  } catch {
   $r.uia_hit_error=$_.Exception.Message
   if ($_.Exception.Message -eq 'Done accessibility hit belongs to another process.') { throw }
  }
  $cursor=New-Object TelegramContactMouse+NativePoint
  if (-not [TelegramContactMouse]::GetCursorPos([ref]$cursor) -or $cursor.X -ne $x -or $cursor.Y -ne $y) { throw 'Mouse moved away from Done; not clicked.' }
  RootCheck
  if ([TelegramContactMouse]::GetForegroundWindow() -ne [IntPtr]::new($h)) { throw 'Foreground changed before Done click.' }
  $r.click_target_verified=$true
  $r.create_runtime_id=$id;$r.submit_method='verified_native_window_mouse_click'
  $r.create_attempted=$true
  [TelegramContactMouse]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
  [TelegramContactMouse]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
  $r.create_invoked=$true
 }
 function Profile([string]$identity,[string]$expectedNumber) {
  RootCheck
  if (@(Classes $root 'class Ui::BoxLayerWidget').Count -ne 0) { throw 'A modal is present.' }
  $profile=Single @(Classes $root 'class Info::Profile::Widget') 'user profile'
  $rows=@(Rows $profile)
  $ids=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and $_.Current.ClassName -eq 'class Ui::FlatLabel' -and $_.Current.Name.ToLowerInvariant() -ceq $identity })
  if ($ids.Count -ne 1) { throw 'Profile username does not match the reserved item.' }
  $title=Single @($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and $_.Current.ClassName -eq 'class Ui::MarqueeLabel' }) 'profile name'
  $add=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.ClassName -eq 'class Ui::SettingsButton' -and $_.Current.IsEnabled -and $_.Current.Name -in @('ADICIONAR CONTATO','ADD CONTACT','Add contact','Add to contacts','ADD TO CONTACTS') })
  $edit=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.ClassName -eq 'class Ui::SettingsButton' -and $_.Current.IsEnabled -and $_.Current.Name -in @('Editar contato','Edit contact') })
  $delete=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.ClassName -eq 'class Ui::SettingsButton' -and $_.Current.IsEnabled -and $_.Current.Name -in @('Apagar contato','Delete contact') })
  if ([string]::IsNullOrEmpty($expectedNumber)) {
   if ($add.Count -ne 1 -or $edit.Count -ne 0 -or $delete.Count -ne 0) { throw 'User already a contact or Add contact is missing.' }
  } elseif ($title.Current.Name -cne $expectedNumber -or $edit.Count -ne 1 -or $delete.Count -ne 1 -or $add.Count -ne 0) { throw 'Saved numeric name and contact state do not match.' }
  return $profile
 }
 function Form {
  RootCheck
  $box=Single @(Classes $root 'class Ui::BoxLayerWidget') 'contact modal'
  $generic=Single @(Classes $box 'class Ui::GenericBox') 'username contact form'
  if ($mode -ne 'open' -and ($generic.GetRuntimeId() -join ',') -ne [string]$p.contact_runtime_id) { throw 'Original username form changed.' }
  $rows=@(Rows $box)
  $title=Single @($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and $_.Current.Name -in @('Novo Contato','New Contact') }) 'New Contact title'
  $inner=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and $_.Current.ClassName -eq 'class Ui::InputField::Inner' })
  if ($inner.Count -ne 3 -or @(Classes $box 'class Ui::PhoneInput').Count -ne 0 -or @(Classes $box 'class AddContactBox').Count -ne 0) { throw 'Unexpected form schema; no phone form fallback.' }
  $script:first=Single @($inner | Where-Object { $_.Current.Name -in @('Nome','First name') }) 'first name'
  $script:last=Single @($inner | Where-Object { $_.Current.Name -in @('Sobrenome','Last name') }) 'last name'
  $script:note=Single @($inner | Where-Object { $_.Current.Name -in @('Nota','Note') }) 'private note'
  foreach ($e in @($script:first,$script:last,$script:note)) {
   if (-not $e.Current.IsEnabled) { throw 'A field is disabled.' }
   [void](Value $e)
  }
  $script:done=Single @($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.ClassName -eq 'class Ui::RoundButton' -and $_.Current.IsEnabled -and $_.Current.Name -in @('Pronto','Done') }) 'Done button'
  [void](Single @($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.Name -in @('Cancelar','Cancel') }) 'Cancel button')
  $r.contact_runtime_id=$generic.GetRuntimeId() -join ',';$r.dialog_runtime_id=$box.GetRuntimeId() -join ',';$r.field_schema_verified=$true
 }
 function Value($e) {
  $v=$null
  if (-not $e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$v) -or $v -isnot [System.Windows.Automation.ValuePattern] -or $v.Current.IsReadOnly) { throw 'Writable ValuePattern unavailable.' }
  return $v
 }
 function Filled {
  Form
  if ([string](Value $script:first).Current.Value -cne $number -or [string](Value $script:last).Current.Value -cne '' -or [string](Value $script:note).Current.Value -cne '') { throw 'Numeric contact fields changed.' }
  $r.fields_verified=$true
 }
 function Missing {
  RootCheck
  $box=Single @(Classes $root 'class Ui::BoxLayerWidget') 'username-not-found modal'
  if ($mode -eq 'dismiss_missing' -and ($box.GetRuntimeId() -join ',') -ne [string]$p.missing_dialog_runtime_id) { throw 'Original missing-username dialog changed.' }
  $rows=@(Rows $box)
  $generic=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Window })
  $texts=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text })
  $buttons=@($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button })
  $expected=@(('Não existe conta no Telegram com o nome de usuário '+$username+'.'),('Username '+$username+' not found.'))
  if ($generic.Count -ne 1 -or $generic[0].Current.ClassName -ne 'class Ui::GenericBox' -or
      $texts.Count -ne 1 -or $texts[0].Current.ClassName -ne 'class Ui::FlatLabel' -or $texts[0].Current.Name -cnotin $expected -or
      $buttons.Count -ne 1 -or $buttons[0].Current.Name -cne 'OK' -or $buttons[0].Current.ClassName -ne 'class Ui::RoundButton' -or -not $buttons[0].Current.IsEnabled -or
      @($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit }).Count -ne 0) { throw 'Dialog is not the exact missing-username result for this item.' }
  return @{box=$box;button=$buttons[0];text=[string]$texts[0].Current.Name}
 }
 function DismissMissing {
  $first=Missing;$dialogId=$first.box.GetRuntimeId() -join ',';$buttonId=$first.button.GetRuntimeId() -join ','
  Start-Sleep -Milliseconds 300
  $second=Missing
  if (($second.box.GetRuntimeId() -join ',') -ne $dialogId -or ($second.button.GetRuntimeId() -join ',') -ne $buttonId -or $first.text -cne $second.text) { throw 'Missing-username result changed between reads.' }
  $r.missing_dialog_runtime_id=$dialogId;$r.not_found_text=$second.text
  $r.not_found_verified=$true;$r.not_found_reads=2;$r.dismiss_runtime_id=$buttonId
  $r.dismiss_attempted=$true;Invoke $second.button;$r.dismiss_invoked=$true;$r.result_absent=$false
  for ($i=0;$i -lt 30;$i++) {
   Start-Sleep -Milliseconds 100;RootCheck
   if (@(Classes $root 'class Ui::BoxLayerWidget').Count -eq 0) { $r.result_absent=$true;break }
  }
  if (-not $r.result_absent) { throw 'Missing-username result did not close; item remains reserved.' }
  $r.state='username_not_found';$r.stage='verified';$r.ok=$true
 }
 RootCheck
 if ($mode -eq 'close_saved') {
  $saved=Profile $username $number
  if (($saved.GetRuntimeId() -join ',') -ne [string]$p.profile_runtime_id) { throw 'Verified saved profile changed; not closed.' }
  $close=Single @((Rows $saved) | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name -in @('Fechar painel','Close panel') }) 'Close panel'
  $r.close_attempted=$true;Invoke $close;$r.close_invoked=$true;$r.profile_absent=$false
  for ($i=0;$i -lt 30;$i++) {
   Start-Sleep -Milliseconds 100;RootCheck
   if (@(Classes $root 'class Info::Profile::Widget').Count -eq 0 -and @(Classes $root 'class Ui::BoxLayerWidget').Count -eq 0) { $r.profile_absent=$true;break }
  }
  if (-not $r.profile_absent) { throw 'Saved profile did not close; addition remains recorded.' }
  $r.profile_runtime_id=[string]$p.profile_runtime_id;$r.profile_verified=$true
  $r.state='saved_profile_closed';$r.stage='verified';$r.ok=$true
  $r | ConvertTo-Json -Depth 8 -Compress;exit 0
 }
 if ($mode -eq 'dismiss_missing') {
  if ($rootId -ne [string]$p.main_runtime_id) { throw 'Original resolving window changed.' }
  DismissMissing;$r | ConvertTo-Json -Depth 8 -Compress;exit 0
 }
 if ($mode -eq 'open') {
  $proc=Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = '+$expectedPid)
  if ([string]::IsNullOrWhiteSpace($proc.CommandLine) -or [string]$proc.CommandLine -match '(?i)(?:^|\s|\")-(?:many|workdir)(?=\s|\"|$)') { throw 'Custom or unknown launch routing is not supported.' }
  $windows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$expectedPid)) | Where-Object { (Visible $_) -and $_.Current.ClassName -eq 'class MainWindow' })
  if ($windows.Count -ne 1 -or ($windows[0].GetRuntimeId() -join ',') -ne $rootId) { throw 'Multiple account windows in one process; routing is ambiguous.' }
  $r.default_instance_route_verified=$true
  if (@(Classes $root 'class Ui::BoxLayerWidget').Count -ne 0 -and $null -ne $p.phone_form_cleanup) {
   $proof=$p.phone_form_cleanup
   if ($rootId -ne [string]$proof.main_runtime_id) { throw 'Original phone window changed; form preserved.' }
   $box=Single @(Classes $root 'class Ui::BoxLayerWidget') 'original phone modal'
   $contact=Single @(Classes $box 'class AddContactBox') 'original phone form'
   if (($contact.GetRuntimeId() -join ',') -ne [string]$proof.contact_runtime_id) { throw 'Original phone form changed; preserved.' }
   $rows=@(Rows $box)
   [void](Single @($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and $_.Current.Name -in @('New Contact','Novo Contato') }) 'phone form title')
   if (@(Classes $box 'class Ui::PhoneInput').Count -ne 1) { throw 'Expected phone input; form preserved.' }
   $cancel=Single @($rows | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name -in @('Cancel','Cancelar') }) 'Cancel phone form'
   $r.phone_form_close_attempted=$true;Invoke $cancel;$r.phone_form_close_invoked=$true
   for ($i=0;$i -lt 30;$i++) { Start-Sleep -Milliseconds 100;RootCheck;if (@(Classes $root 'class Ui::BoxLayerWidget').Count -eq 0) { break } }
   $r.phone_form_absent=(@(Classes $root 'class Ui::BoxLayerWidget').Count -eq 0)
   if (-not $r.phone_form_absent) { throw 'Phone form did not close.' }
  }
  if (@(Classes $root 'class Ui::BoxLayerWidget').Count -ne 0) { throw 'Existing dialog preserved; return to the main chat first.' }
  $profiles=@(Classes $root 'class Info::Profile::Widget')
  if ($profiles.Count -gt 0) {
   # Only the last proven saved username in this same account may be closed.
   if ($p.retry_unsaved_profile_runtime_id) {
    $old=Profile $username ''
    if (($old.GetRuntimeId() -join ',') -ne [string]$p.retry_unsaved_profile_runtime_id) { throw 'Original unsaved profile changed; preserved.' }
   } else {
    if ($null -eq $p.previous) { throw 'Existing profile preserved; close it manually before starting.' }
    $old=Profile ([string]$p.previous.username).ToLowerInvariant() ([string]$p.previous.number)
   }
   $close=Single @((Rows $old) | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name -in @('Fechar painel','Close panel') }) 'Close panel'
   Invoke $close
   for ($i=0;$i -lt 20;$i++) { Start-Sleep -Milliseconds 100;RootCheck;if (@(Classes $root 'class Info::Profile::Widget').Count -eq 0) { break } }
   if (@(Classes $root 'class Info::Profile::Widget').Count -ne 0) { throw 'Previous profile did not close.' }
  }
  $r.stage='resolve_requested'
  Start-Process -FilePath $path -ArgumentList @('--',('tg://resolve?domain='+$username.Substring(1)+'&profile')) -WorkingDirectory ([IO.Path]::GetDirectoryName($path)) | Out-Null
  $profile=$null;$lastReadError='';$r.resolution_read_retries=0
  for ($i=0;$i -lt 60;$i++) {
   Start-Sleep -Milliseconds 150;RootCheck
   # The accessibility tree may be replaced while the deep link opens.
   # Retry only this read-only observation; never replay any button action.
   $hasModal=$false
   try {
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($h))
    $hasModal=(@(Classes $root 'class Ui::BoxLayerWidget').Count -ne 0)
    if (-not $hasModal) {
     $profiles=@(Classes $root 'class Info::Profile::Widget')
     if ($profiles.Count -eq 1) {
      $identity=@((Rows $profiles[0]) | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and $_.Current.ClassName -eq 'class Ui::FlatLabel' -and $_.Current.Name.ToLowerInvariant() -ceq $username })
      if ($identity.Count -eq 1) { $profile=Profile $username '' }
     }
    }
   } catch {
    $lastReadError=$_.Exception.Message;$r.resolution_read_retries++
    continue
   }
   if ($hasModal) {
    DismissMissing;$r | ConvertTo-Json -Depth 8 -Compress;exit 0
   }
   if ($null -ne $profile) { break }
  }
  if ($null -eq $profile) { throw ('Username profile resolution timed out; no automatic retry. Last read: '+$lastReadError) }
  $r.profile_runtime_id=$profile.GetRuntimeId() -join ',';$r.username_verified=$true
  $add=Single @((Rows $profile) | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.ClassName -eq 'class Ui::SettingsButton' -and $_.Current.Name -in @('ADICIONAR CONTATO','ADD CONTACT','Add contact','Add to contacts','ADD TO CONTACTS') }) 'Add contact'
  [void](Profile $username '');Invoke $add;$r.add_invoked=$true
  for ($i=0;$i -lt 30;$i++) { Start-Sleep -Milliseconds 100;RootCheck;if (@(Classes $root 'class Ui::BoxLayerWidget').Count -eq 1) { break } }
  Form
  if ([string](Value $script:note).Current.Value -cne '') { throw 'Unexpected prefilled note; form preserved.' }
  $r.state='username_form_open';$r.stage='verified'
 } else {
  $r.profile_runtime_id=[string]$p.profile_runtime_id
  Form;$r.username_verified=$true
  if ($mode -eq 'fill') {
   if ([string](Value $script:note).Current.Value -cne '') { throw 'Existing note preserved.' }
   $r.fields_written=$true
   (Value $script:first).SetValue($number);(Value $script:last).SetValue('')
   Filled;$r.stage='verified';$r.state='filled'
  } else {
   Filled
   ClickDone
   # Do not interpret the UIA snapshot used for input as a saved-profile proof.
   # The independent read-only inspector verifies the resulting contact twice.
   Start-Sleep -Milliseconds 300;RootCheck
   $r.state='awaiting_profile';$r.stage='submitted';$r.profile_verified=$false

  }
 }
 $r.ok=$true;$r | ConvertTo-Json -Depth 8 -Compress
} catch { $r.errors=@($_.Exception.Message);$r | ConvertTo-Json -Depth 8 -Compress;exit 1 }
