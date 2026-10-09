"""First-use activation screen; no contact database or window automation."""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from activation_codec import (ActivationError, device_id, display_device,
                              load_public_key, save_activation, saved_activation_valid)


def _icon(root, base):
    try:
        root.iconbitmap(default=str(base / 'gold_ingot.ico'))
    except tk.TclError:
        try:
            root._activation_icon = tk.PhotoImage(master=root, file=str(base / 'gold_ingot.png'))
            root.iconphoto(True, root._activation_icon)
        except tk.TclError:
            pass


def ensure_activated(root, base, data):
    base, data = Path(base), Path(data)
    _icon(root, base)
    try:
        device = device_id()
        public_key = load_public_key(base / 'activation_public_key.json')
    except ActivationError as error:
        messagebox.showerror('无法启动', str(error), parent=root)
        return False
    record = data / 'activation.json'
    if saved_activation_valid(record, device, public_key):
        return True
    dialog = tk.Toplevel(root)
    dialog.title('平安喜乐助手 - 首次使用激活')
    dialog.resizable(False, False)
    frame = ttk.Frame(dialog, padding=22)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='请将设备码发给授权人，收到激活码后粘贴到下方。\n'
                         '这台电脑激活成功后，以后打开无需再次输入。').pack(anchor='w', pady=(0, 14))
    ttk.Label(frame, text='本机设备码').pack(anchor='w')
    device_text = tk.StringVar(master=dialog, value=display_device(device))
    device_entry = ttk.Entry(frame, textvariable=device_text, state='readonly', width=82)
    device_entry.pack(fill='x', pady=5)

    def copy_device():
        dialog.clipboard_clear()
        dialog.clipboard_append(device_text.get())
        status.set('设备码已复制，请发给授权人。')

    ttk.Button(frame, text='复制设备码', command=copy_device).pack(anchor='w', pady=(0, 12))
    ttk.Label(frame, text='激活码').pack(anchor='w')
    entry = tk.Text(frame, width=78, height=5, wrap='char')
    entry.pack(fill='x', pady=5)
    status = tk.StringVar(master=dialog)
    ttk.Label(frame, textvariable=status, foreground='#9b3600', wraplength=610).pack(anchor='w', pady=10)
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x')
    result = [False]

    def activate():
        try:
            save_activation(record, entry.get('1.0', 'end'), device, public_key)
        except (ActivationError, OSError) as error:
            status.set(str(error))
            return
        result[0] = True
        dialog.destroy()

    ttk.Button(buttons, text='激活并打开助手', command=activate).pack(side='right')
    ttk.Button(buttons, text='退出', command=dialog.destroy).pack(side='right', padx=8)
    dialog.bind('<Escape>', lambda event: dialog.destroy())
    dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
    dialog.grab_set()
    entry.focus_set()
    root.wait_window(dialog)
    return result[0]

