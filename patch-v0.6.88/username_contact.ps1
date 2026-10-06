# Username contact actions: bind the profile and modal before any input.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$actionClock=[Diagnostics.Stopwatch]::StartNew()
$r=[ordered]@{ok=$false;scope='username_contact';stage='initial';state='review';process_path_verified=$false;default_instance_route_verified=$false;username_verified=$false;field_schema_verified=$false;fields_verified=$false;fields_written=$false;add_invoked=$false;create_attempted=$false;create_invoked=$false;profile_verified=$false;final_invite_clicked=$false;contact_database_updated=$false;errors=@()}
try {
 Add-Type -AssemblyName UIAutomationClient
 Add-Type -AssemblyName UIAutomationTypes
 $p=$env:TG_ACTION_PAYLOAD | ConvertFrom-Json
 $mode=[string]$p.mode;$username=([string]$p.username).ToLowerInvariant();$number=[string]$p.number
 if ($mode -notin @('open','fill','submit','dismiss_missing','close_saved','reopen_submitted') -or $username -notmatch '^@[a-z0-9_]{1,32}$') { throw 'Invalid username action.' }
 if ($mode -in @('fill','submit','close_saved','reopen_submitted') -and $number -notmatch '^[1-9][0-9]{0,5}$') { throw 'Invalid contact number.' }
 $h=[Int64]::Parse($env:TG_INSPECT_HWND);$expectedPid=[Int32]::Parse($env:TG_INSPECT_PID)
 $path=[IO.Path]::GetFullPath([string]$p.executable_path)
 $r.mode=$mode;$r.username=$username;$r.number=$number;$r.window_handle=$h;$r.process_id=$expectedPid;$r.executable_path=$path
 # UIA bounds and Win32 cursor coordinates must use the same physical pixels.
 Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class TelegramContactDpi {
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [DllImport("kernel32.dll",SetLastError=true)] private static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] private static extern bool QueryFullProcessImageName(IntPtr process,uint flags,StringBuilder path,ref int size);
 [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
 public static string ProcessPath(int pid) {
  var handle=OpenProcess(0x1000,false,pid);
  if (handle==IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
  try {
   var path=new StringBuilder(32768);int size=path.Capacity;
   if (!QueryFullProcessImageName(handle,0,path,ref size)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
   return path.ToString();
  } finally { CloseHandle(handle); }
 }
}
'@
 $dpiPrevious=[TelegramContactDpi]::SetThreadDpiAwarenessContext([IntPtr]::new(-4))
 if ($dpiPrevious -eq [IntPtr]::Zero) { throw 'Cannot enable physical-pixel DPI context; no action.' }
 $r.physical_pixel_context=$true
 $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($h))
 if ($null -eq $root -or $root.Current.ProcessId -ne $expectedPid -or $root.Current.ClassName -ne 'class MainWindow') { throw 'Bound Telegram window changed.' }
 $rootId=$root.GetRuntimeId() -join ',';$r.main_runtime_id=$rootId
 if ($mode -in @('fill','submit','close_saved','reopen_submitted') -and ($rootId -ne [string]$p.main_runtime_id -or [string]::IsNullOrWhiteSpace([string]$p.profile_runtime_id))) { throw 'Original profile/window identity missing.' }
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
  $processPath=[TelegramContactDpi]::ProcessPath($expectedPid)
  if (-not [string]::Equals([IO.Path]::GetFullPath($processPath),$path,[StringComparison]::OrdinalIgnoreCase)) { throw 'Executable changed.' }
  $now=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($h))
  if ($null -eq $now -or $now.Current.ProcessId -ne $expectedPid -or ($now.GetRuntimeId() -join ',') -ne $rootId -or -not (Visible $now)) { throw 'Window changed.' }
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
 [StructLayout(LayoutKind.Sequential)] public struct Point { public int X,Y; }
 [DllImport("user32.dll",SetLastError=true)] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern bool GetCursorPos(out Point point);
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(Point point);
 [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr window,uint flags);
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
 public static int MouseDown() { return Send(new Input[] { new Input { Type=0,Data=new InputUnion { Mouse=new MouseInput { Flags=2 } } } }); }
 public static int MouseUp() { return Send(new Input[] { new Input { Type=0,Data=new InputUnion { Mouse=new MouseInput { Flags=4 } } } }); }
 public static int NativeInputSize() { return Marshal.SizeOf(typeof(Input)); }
}
'@
  $script:keyboardReady=$true
 }
 function ActivateKeyboard {
  InitKeyboard;RootCheck
  if ([TelegramContactInput]::GetForegroundWindow() -ne [IntPtr]::new($h)) {
   [void][TelegramContactInput]::SetForegroundWindow([IntPtr]::new($h))
   for ($i=0;$i -lt 5;$i++) {
    if ([TelegramContactInput]::GetForegroundWindow() -eq [IntPtr]::new($h)) { break }
    Start-Sleep -Milliseconds 40
   }
  }
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
  ActivateKeyboard;Filled
  $doneId=$script:done.GetRuntimeId() -join ','
  $bounds=$script:done.Current.BoundingRectangle
  $x=[int][Math]::Floor($bounds.Left+$bounds.Width/2)
  $y=[int][Math]::Floor($bounds.Top+$bounds.Height/2)
  $r.submit_method='verified_modal_done_mouse_sendinput_close_reopen';$r.refresh_policy='mouse_close_profile_then_reopen_without_old_saved_state_read'
  $r.create_runtime_id=$doneId;$r.click_x=$x;$r.click_y=$y
  $r.click_bounds=@{left=$bounds.Left;top=$bounds.Top;width=$bounds.Width;height=$bounds.Height}
  if (-not [TelegramContactInput]::SetCursorPos($x,$y)) { throw 'Cannot move cursor to Done.' }
  Start-Sleep -Milliseconds 150
  for ($read=0;$read -lt 2;$read++) {
   Filled
   if (($script:done.GetRuntimeId() -join ',') -ne $doneId) { throw 'Done button changed before mouse input.' }
   $now=$script:done.Current.BoundingRectangle
   if ($now.Left -ne $bounds.Left -or $now.Top -ne $bounds.Top -or $now.Width -ne $bounds.Width -or $now.Height -ne $bounds.Height) { throw 'Done moved before mouse input.' }
   $point=New-Object TelegramContactInput+Point
   if (-not [TelegramContactInput]::GetCursorPos([ref]$point) -or $point.X -ne $x -or $point.Y -ne $y) { throw 'Cursor moved before Done click.' }
   $hitWindow=[TelegramContactInput]::WindowFromPoint($point)
   if ([TelegramContactInput]::GetAncestor($hitWindow,2) -ne [IntPtr]::new($h) -or [TelegramContactInput]::GetForegroundWindow() -ne [IntPtr]::new($h)) { throw 'Done point is covered by another window.' }
   $box=Single @(Classes $root 'class Ui::BoxLayerWidget') 'original contact modal'
   if (($box.GetRuntimeId() -join ',') -ne $r.dialog_runtime_id) { throw 'Original modal changed before Done click.' }
   $modal=$box.Current.BoundingRectangle
   if ($bounds.Left -lt $modal.Left -or $bounds.Top -lt $modal.Top -or $bounds.Right -gt $modal.Right -or $bounds.Bottom -gt $modal.Bottom) { throw 'Done bounds outside original modal.' }
   $r.click_modal_runtime_id=$box.GetRuntimeId() -join ','
   $r.click_modal_bounds=@{left=$modal.Left;top=$modal.Top;width=$modal.Width;height=$modal.Height}
   $r.click_modal_containment_verified=$true;$r.click_native_window_verified=$true
   # Qt's UIA ElementFromPoint may expose the chat underneath the modal.
   # Keep that observation diagnostic; it cannot override the bound modal tree.
   try {
    $hit=[System.Windows.Automation.AutomationElement]::FromPoint([System.Windows.Point]::new($x,$y))
    $r.click_hit_runtime_id=$hit.GetRuntimeId() -join ','
    $r.click_hit_name=$hit.Current.Name;$r.click_hit_class=$hit.Current.ClassName
   } catch { $r.click_hit_error=$_.Exception.Message }
   foreach ($key in @(1,2,16,17,18,91,92)) {
    if (([TelegramContactInput]::GetAsyncKeyState($key) -band 0x8000) -ne 0) { throw 'Mouse button or modifier pressed; no click sent.' }
   }
   if ($read -eq 0) { Start-Sleep -Milliseconds 100 }
  }
  $r.click_target_verified=$true;$r.click_target_reads=2
  $r.click_target_runtime_id=$doneId;$r.click_target_name=$script:done.Current.Name;$r.click_target_class=$script:done.Current.ClassName
  $r.create_attempted=$true
  $r.submit_mouse_down_count=[TelegramContactInput]::MouseDown()
  try { Start-Sleep -Milliseconds 30 } finally { $r.submit_mouse_up_count=[TelegramContactInput]::MouseUp() }
  $r.create_invoked=$true;$r.done_input_at_ms=$actionClock.ElapsedMilliseconds
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
 function CloseProfileForReopen($profile) {
  $close=Single @((Rows $profile) | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name -in @('Close panel','Fechar painel') }) 'profile close button'
  ActivateKeyboard;RootCheck
  if (@(Classes $root 'class Ui::BoxLayerWidget').Count -ne 0) { throw 'Modal covers profile close; no mouse input.' }
  $id=$close.GetRuntimeId() -join ',';$bounds=$close.Current.BoundingRectangle
  $profileBounds=$profile.Current.BoundingRectangle
  if ($bounds.Left -lt $profileBounds.Left -or $bounds.Top -lt $profileBounds.Top -or $bounds.Right -gt $profileBounds.Right -or $bounds.Bottom -gt $profileBounds.Bottom) { throw 'Close button outside profile; no click.' }
  $x=[int][Math]::Floor($bounds.Left+$bounds.Width/2);$y=[int][Math]::Floor($bounds.Top+$bounds.Height/2)
  if (-not [TelegramContactInput]::SetCursorPos($x,$y)) { throw 'Cannot move cursor to profile close.' }
  $point=New-Object TelegramContactInput+Point
  if (-not [TelegramContactInput]::GetCursorPos([ref]$point) -or $point.X -ne $x -or $point.Y -ne $y) { throw 'Profile close cursor moved.' }
  if ([TelegramContactInput]::GetAncestor([TelegramContactInput]::WindowFromPoint($point),2) -ne [IntPtr]::new($h) -or [TelegramContactInput]::GetForegroundWindow() -ne [IntPtr]::new($h)) { throw 'Profile close covered by another window.' }
  RootCheck
  if (($close.GetRuntimeId() -join ',') -ne $id -or -not $close.Current.IsEnabled) { throw 'Profile close changed.' }
  $now=$close.Current.BoundingRectangle
  if ($now.Left -ne $bounds.Left -or $now.Top -ne $bounds.Top -or $now.Width -ne $bounds.Width -or $now.Height -ne $bounds.Height) { throw 'Profile close moved; no click.' }
  foreach ($key in @(1,2)) {
   if (([TelegramContactInput]::GetAsyncKeyState($key) -band 0x8000) -ne 0) { throw 'Mouse button pressed; no profile close input.' }
  }
  $r.refresh_close_attempted=$true;$r.refresh_close_runtime_id=$id
  $r.refresh_close_mouse_down_count=[TelegramContactInput]::MouseDown()
  try { Start-Sleep -Milliseconds 30 } finally { $r.refresh_close_mouse_up_count=[TelegramContactInput]::MouseUp() }
  for ($i=0;$i -lt 40;$i++) {
   RootCheck
   if (@(Classes $root 'class Info::Profile::Widget').Count -eq 0) { $r.refresh_previous_profile_absent=$true;return }
   Start-Sleep -Milliseconds 50
  }
  throw 'Profile close input sent but profile still visible; no repeated click.'
 }
 function ReopenSubmittedProfile {
  $r.stage='refresh_profile';$r.profile_refresh_attempted=$false;$r.refresh_begin_at_ms=$actionClock.ElapsedMilliseconds
  RootCheck
  $modals=@(Classes $root 'class Ui::BoxLayerWidget')
  if ($modals.Count -gt 1) { throw 'Multiple modals after Done; no blind close.' }
  if ($modals.Count -eq 1) {
   # The user authorizes closing the original form after the one Done input.
   # Preserve other dialogs so errors/restrictions cannot be hidden.
   try {
    Filled
    $cancel=Single @((Rows $modals[0]) | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name -in @('Cancel','Cancelar') }) 'original submitted form cancel'
    # Saving may close the modal between the two accessibility reads.
    if (@(Classes $root 'class Ui::BoxLayerWidget').Count -ne 0) {
     $r.submitted_form_close_attempted=$true;Invoke $cancel;$r.submitted_form_close_invoked=$true
    }
   } catch {
    RootCheck
    if (@(Classes $root 'class Ui::BoxLayerWidget').Count -ne 0) { throw }
    $r.submitted_form_closed_by_save=$true
   }
   for ($i=0;$i -lt 40;$i++) {
    RootCheck
    if (@(Classes $root 'class Ui::BoxLayerWidget').Count -eq 0) { break }
    Start-Sleep -Milliseconds 50
   }
  }
  if (@(Classes $root 'class Ui::BoxLayerWidget').Count -ne 0) { throw 'Original form did not close after Done; no second submission.' }
  $r.contact_form_absent=$true
  # Close the old profile without interpreting its name or saved-contact state.
  $profiles=@()
  for ($i=0;$i -lt 4;$i++) {
   RootCheck;$profiles=@(Classes $root 'class Info::Profile::Widget')
   if ($profiles.Count -gt 0) { break }
   if ($i -lt 3) { Start-Sleep -Milliseconds 25 }
  }
  if ($profiles.Count -gt 1) { throw 'Multiple profiles before reopen; no blind click.' }
  if ($profiles.Count -eq 1) {
   $r.refresh_previous_profile_runtime_id=$profiles[0].GetRuntimeId() -join ','
   CloseProfileForReopen $profiles[0]
  } else { $r.refresh_previous_profile_absent=$true }
  $r.refresh_close_before_reopen=$true
  $windows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$expectedPid)) | Where-Object { (Visible $_) -and $_.Current.ClassName -eq 'class MainWindow' })
  if ($windows.Count -ne 1 -or ($windows[0].GetRuntimeId() -join ',') -ne $rootId) { throw 'Profile refresh routing ambiguous; no link sent.' }
  RootCheck;$r.profile_refresh_attempted=$true;$r.refresh_link_at_ms=$actionClock.ElapsedMilliseconds
  Start-Process -FilePath $path -ArgumentList @('--',('tg://resolve?domain='+$username.Substring(1)+'&profile')) -WorkingDirectory ([IO.Path]::GetDirectoryName($path)) | Out-Null
  for ($i=0;$i -lt 60;$i++) {
   if ($i -gt 0) { Start-Sleep -Milliseconds 50 };RootCheck
   if (@(Classes $root 'class Ui::BoxLayerWidget').Count -ne 0) { throw 'Unexpected modal after refresh; preserved.' }
   $profiles=@(Classes $root 'class Info::Profile::Widget')
   if ($profiles.Count -ne 1) { continue }
   $identity=@((Rows $profiles[0]) | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text -and $_.Current.ClassName -eq 'class Ui::FlatLabel' -and $_.Current.Name.ToLowerInvariant() -ceq $username })
   if ($identity.Count -ne 1) { continue }
   $r.profile_runtime_id=$profiles[0].GetRuntimeId() -join ','
   $r.profile_refresh_username=$username;$r.profile_reopened=$true;$r.refresh_reopened_at_ms=$actionClock.ElapsedMilliseconds
   return
  }
  throw 'Same username profile did not reopen; no repeated submission.'
 }
 function SavedRetryProfile {
  if (-not $p.retry_unsaved_profile_runtime_id -or [string]$p.retry_expected_number -notmatch '^[1-9][0-9]{0,5}$') { return $false }
  try { $saved=Profile $username ([string]$p.retry_expected_number) } catch { return $false }
  $r.profile_runtime_id=$saved.GetRuntimeId() -join ','
  $r.username_verified=$true;$r.profile_verified=$true
  $r.stage='existing_saved_profile';$r.state='existing_saved_profile';$r.ok=$true
  return $true
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
 if ($mode -eq 'reopen_submitted') {
  if ($p.submission_dispatched -ne $true) { throw 'Original dispatched submit proof missing; no navigation.' }
  ReopenSubmittedProfile
  $r.ok=$true;$r.stage='reopened';$r.state='profile_reopened'
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
    if (SavedRetryProfile) { $r | ConvertTo-Json -Depth 8 -Compress;exit 0 }
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
      if ($identity.Count -eq 1) {
       if (SavedRetryProfile) { $r | ConvertTo-Json -Depth 8 -Compress;exit 0 }
       $profile=Profile $username ''
      }
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
   ReopenSubmittedProfile
   $r.state='awaiting_profile';$r.stage='submitted';$r.profile_verified=$false

  }
 }
 $r.ok=$true;$r | ConvertTo-Json -Depth 8 -Compress
} catch { $r.errors=@($_.Exception.Message);$r | ConvertTo-Json -Depth 8 -Compress;exit 1 }
