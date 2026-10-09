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
 function InitKeyboard {
  if ($script:keyboardReady) { return }
  Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class TelegramContactInput {
 [StructLayout(LayoutKind.Sequential)] public struct MouseInput { public int X,Y; public uint Data,Flags,Time; public UIntPtr Extra; }
 [StructLayout(LayoutKind.Sequential)] public struct KeyboardInput { public ushort Key,Scan; public uint Flags,Time; public UIntPtr Extra; }
 [StructLayout(LayoutKind.Explicit)] public struct InputUnion { [FieldOffset(0)] public MouseInput Mouse; [FieldOffset(0)] public KeyboardInput Keyboard; }
 [StructLayout(LayoutKind.Sequential)] public struct Input { public uint Type; public InputUnion Data; }
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int key);
 [DllImport("user32.dll")] private static extern uint MapVirtualKey(uint code,uint type);
 [DllImport("user32.dll",SetLastError=true)] private static extern uint SendInput(uint count,Input[] inputs,int size);
 private static Input Key(ushort key,bool up) {
  return new Input { Type=1,Data=new InputUnion { Keyboard=new KeyboardInput { Scan=(ushort)MapVirtualKey(key,0),Flags=(uint)(8 | (up ? 2 : 0)) } } };
 }
 private static int Send(Input[] inputs) {
  uint count=SendInput((uint)inputs.Length,inputs,Marshal.SizeOf(typeof(Input)));
  if (count!=inputs.Length) throw new Win32Exception(Marshal.GetLastWin32Error(),"Keyboard input not fully delivered: "+count+"/"+inputs.Length);
  return (int)count;
 }
 public static int ReplaceText(string text) {
  var inputs=new List<Input> { Key(17,false),Key(65,false),Key(65,true),Key(17,true),Key(8,false),Key(8,true) };
  foreach (char c in text) {
   inputs.Add(new Input { Type=1,Data=new InputUnion { Keyboard=new KeyboardInput { Scan=c,Flags=4 } } });
   inputs.Add(new Input { Type=1,Data=new InputUnion { Keyboard=new KeyboardInput { Scan=c,Flags=6 } } });
  }
  try { return Send(inputs.ToArray()); }
  catch { try { Send(new Input[] { Key(17,true),Key(65,true),Key(8,true) }); } catch {} throw; }
 }
 public static uint EnterScanCode() { return MapVirtualKey(13,0); }
 public static int EnterDown() { return Send(new Input[] { Key(13,false) }); }
 public static int EnterUp() { return Send(new Input[] { Key(13,true) }); }
 public static int NativeInputSize() { return Marshal.SizeOf(typeof(Input)); }
}
'@
  $script:keyboardReady=$true
 }
 function ActivateKeyboard {
  InitKeyboard;RootCheck
  [void][TelegramContactInput]::SetForegroundWindow([IntPtr]::new($h));Start-Sleep -Milliseconds 200
  if ([TelegramContactInput]::GetForegroundWindow() -ne [IntPtr]::new($h)) { throw 'Telegram not foreground; no keyboard input.' }
  foreach ($key in @(16,17,18,91,92)) {
   if (([TelegramContactInput]::GetAsyncKeyState($key) -band 0x8000) -ne 0) { throw 'Release Shift/Ctrl/Alt/Windows keys before automation.' }
  }
 }
 function FocusBound($element,[string]$class,[string]$label) {
  RootCheck
  $id=$element.GetRuntimeId() -join ','
  $element.SetFocus();Start-Sleep -Milliseconds 150
  for ($read=0;$read -lt 2;$read++) {
   RootCheck
   $focused=[System.Windows.Automation.AutomationElement]::FocusedElement
   if ($null -ne $focused) {
    $r.actual_focus_runtime_id=$focused.GetRuntimeId() -join ','
    $r.actual_focus_name=$focused.Current.Name;$r.actual_focus_class=$focused.Current.ClassName
   }
   if ($null -eq $focused -or ($focused.GetRuntimeId() -join ',') -ne $id -or $focused.Current.ProcessId -ne $expectedPid -or -not $focused.Current.HasKeyboardFocus -or $focused.Current.ClassName -ne $class -or $focused.Current.Name -cne $label -or [TelegramContactInput]::GetForegroundWindow() -ne [IntPtr]::new($h)) {
    throw ('Keyboard focus mismatch for '+$label+'; no input sent.')
   }
   if ($read -eq 0) { Start-Sleep -Milliseconds 150 }
  }
  return $id
 }
 function SubmitDone {
  # Ui::RoundButton is not focusable on the observed Telegram build.
  # EditContactBox wires last-name submits() to its save callback.
  ActivateKeyboard;Filled
  $lastId=$script:last.GetRuntimeId() -join ','
  $r.submit_focus_name=$script:last.Current.Name
  $id=FocusBound $script:last 'class Ui::InputField::Inner' $r.submit_focus_name
  Filled
  if (($script:last.GetRuntimeId() -join ',') -ne $lastId -or $id -ne $lastId) { throw 'Original last-name field changed; no Enter sent.' }
  $focused=[System.Windows.Automation.AutomationElement]::FocusedElement
  if ($null -eq $focused -or ($focused.GetRuntimeId() -join ',') -ne $lastId -or -not $focused.Current.HasKeyboardFocus -or [TelegramContactInput]::GetForegroundWindow() -ne [IntPtr]::new($h)) { throw 'Last-name focus changed before Enter; not submitted.' }
  foreach ($key in @(16,17,18,91,92)) {
   if (([TelegramContactInput]::GetAsyncKeyState($key) -band 0x8000) -ne 0) { throw 'Modifier key pressed before Enter; not submitted.' }
  }
  $r.submit_focus_runtime_id=$id;$r.focus_target_verified=$true;$r.focus_reads=2
  $r.create_runtime_id=$script:done.GetRuntimeId() -join ','
  $r.submit_method='verified_focused_last_name_enter_sendinput';$r.submit_scan_code=[TelegramContactInput]::EnterScanCode()
  if ($r.submit_scan_code -ne 28) { throw 'Unexpected Enter scan code; not submitted.' }
  $r.create_attempted=$true
  $r.submit_key_down_count=[TelegramContactInput]::EnterDown()
  try { Start-Sleep -Milliseconds 100 } finally { $r.submit_key_up_count=[TelegramContactInput]::EnterUp() }
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
   ActivateKeyboard;Form
   [void](FocusBound $script:first 'class Ui::InputField::Inner' $script:first.Current.Name)
   $r.fields_written=$true
   $r.first_name_input_count=[TelegramContactInput]::ReplaceText($number)
   Start-Sleep -Milliseconds 200;Form
   if ([string](Value $script:first).Current.Value -cne $number) { throw 'First name not accepted after keyboard input.' }
   [void](FocusBound $script:last 'class Ui::InputField::Inner' $script:last.Current.Name)
   $r.last_name_input_count=[TelegramContactInput]::ReplaceText('')
   Start-Sleep -Milliseconds 200
   Filled;$r.fill_method='verified_keyboard_input';$r.stage='verified';$r.state='filled'
  } else {
   Filled
   SubmitDone
   # Do not interpret the UIA snapshot used for input as a saved-profile proof.
   # The independent read-only inspector verifies the resulting contact twice.
   Start-Sleep -Milliseconds 300;RootCheck
   $r.state='awaiting_profile';$r.stage='submitted';$r.profile_verified=$false

  }
 }
 $r.ok=$true;$r | ConvertTo-Json -Depth 8 -Compress
} catch { $r.errors=@($_.Exception.Message);$r | ConvertTo-Json -Depth 8 -Compress;exit 1 }
