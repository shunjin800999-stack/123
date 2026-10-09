"""Read-only window enumeration; candidates are NOT verified logged-in accounts."""
import ctypes
import os
from ctypes import wintypes
from pathlib import PureWindowsPath


def is_telegram_process(path):
    # Titles contain chat names and unrelated apps can mention Telegram.
    # An unreadable executable path must not be treated as a verified match.
    return bool(path) and PureWindowsPath(path).name.casefold() == 'telegram.exe'


def scan_windows(include_all=False):
    if os.name != 'nt':
        raise RuntimeError('窗口扫描只能在你的 Windows 电脑运行')
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.IsIconic.argtypes = [wintypes.HWND]
    user.IsIconic.restype = wintypes.BOOL
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextLengthW.restype = ctypes.c_int
    user.GetWindowTextW.argtypes = [wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.GetWindowTextW.restype = ctypes.c_int
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    found = []
    errors = []

    def visit(hwnd, _):
        try:
            if not user.IsWindowVisible(hwnd):
                return True
            length = user.GetWindowTextLengthW(hwnd)
            if not length:
                return True
            title_buffer = ctypes.create_unicode_buffer(length+1)
            user.GetWindowTextW(hwnd,title_buffer,length+1)
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value == os.getpid():
                return True
            path = ''
            process = kernel.OpenProcess(0x1000,False,pid.value)
            if process:
                try:
                    buffer = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(buffer))
                    if kernel.QueryFullProcessImageNameW(process,0,buffer,ctypes.byref(size)):
                        path = buffer.value
                finally:
                    kernel.CloseHandle(process)
            candidate = is_telegram_process(path)
            if candidate or include_all:
                found.append({'hwnd':int(hwnd),'pid':pid.value,'title':title_buffer.value,
                              'path':path,'minimized':bool(user.IsIconic(hwnd)),
                              'candidate':candidate})
        except Exception as error:
            errors.append(str(error))
        return True

    callback = callback_type(visit)
    if not user.EnumWindows(callback,0):
        raise ctypes.WinError(ctypes.get_last_error())
    if errors:
        raise RuntimeError('部分窗口读取失败，请重试：'+errors[0])
    return sorted(found,key=lambda row:(row['path'].lower(),row['pid'],row['hwnd']))
