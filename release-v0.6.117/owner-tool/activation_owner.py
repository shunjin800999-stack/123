"""Private operator GUI. This directory is distributed separately from the client."""
import hashlib
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from activation_codec import ActivationError, public_key_from_json
from issuer import authority_public, build_installation, initialize_authority, issue_activation

BASE = Path(__file__).resolve().parent


class OwnerTool:
    def __init__(self, root):
        self.root = root
        root.title('平安喜乐助手 - 激活码生成工具 v0.6.117')
        root.geometry('780x580')
        root.minsize(650, 520)
        try:
            root.iconbitmap(default=str(BASE / 'gold_ingot.ico'))
        except tk.TclError:
            pass
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='这个工具只由授权人保管。首次初始化一次，随后可为多台电脑生成激活码。').pack(anchor='w')
        controls = ttk.Frame(frame)
        controls.pack(fill='x', pady=12)
        ttk.Button(controls, text='首次初始化授权工具', command=self.initialize).pack(side='left')
        ttk.Button(controls, text='制作新电脑安装包', command=self.package).pack(side='left', padx=10)
        self.authority = tk.StringVar(master=root)
        ttk.Label(frame, textvariable=self.authority, wraplength=700).pack(anchor='w', pady=(0, 12))
        ttk.Label(frame, text='收到的设备码（PA1- 开头）').pack(anchor='w')
        self.device = tk.Text(frame, height=3, wrap='char')
        self.device.pack(fill='x', pady=6)
        ttk.Button(frame, text='生成这台电脑的激活码', command=self.generate).pack(anchor='w', pady=6)
        ttk.Label(frame, text='生成的激活码（PAK1. 开头）').pack(anchor='w', pady=(10, 0))
        self.code = tk.Text(frame, height=5, wrap='char', state='disabled')
        self.code.pack(fill='x', pady=6)
        ttk.Button(frame, text='复制激活码', command=self.copy).pack(anchor='w')
        self.status = tk.StringVar(master=root)
        ttk.Label(frame, textvariable=self.status, wraplength=700, foreground='#9b3600').pack(anchor='w', pady=12)
        ttk.Label(frame, text='备份整个工具文件夹及其中的“授权密钥”文件夹；只把制作好的助手安装包发给使用者。',
                  wraplength=700).pack(anchor='w', side='bottom')
        self.refresh()

    def refresh(self):
        try:
            public = authority_public(BASE)
            n, _ = public_key_from_json(public)
            fingerprint = hashlib.sha256(n.to_bytes(256, 'big')).hexdigest()[:16].upper()
            self.authority.set('授权工具已就绪；授权标识：' + fingerprint)
        except ActivationError as error:
            self.authority.set(str(error))

    def initialize(self):
        try:
            initialize_authority(BASE)
        except (ActivationError, OSError) as error:
            messagebox.showerror('初始化未完成', str(error), parent=self.root)
            return
        self.refresh()
        self.status.set('授权工具已就绪。请先制作新电脑安装包，并备份“授权密钥”文件夹。再次点击初始化不会更换已有密钥。')

    def package(self):
        try:
            public = authority_public(BASE)
        except ActivationError as error:
            messagebox.showerror('工具未就绪', str(error), parent=self.root)
            return
        template = filedialog.askopenfilename(parent=self.root, title='选择下载的新电脑安装模板',
                                              filetypes=[('ZIP 安装模板', '*.zip')])
        if not template:
            return
        output = filedialog.asksaveasfilename(parent=self.root, title='保存可分发的新电脑安装包',
                                              initialfile='平安喜乐助手-v0.6.117-新电脑安装包.zip',
                                              defaultextension='.zip', filetypes=[('ZIP 安装包', '*.zip')])
        if not output:
            return
        self.status.set('正在制作新电脑安装包，请稍候。')
        self.root.update_idletasks()
        try:
            build_installation(template, output, public)
        except ActivationError as error:
            self.status.set(str(error))
            messagebox.showerror('安装包未生成', str(error), parent=self.root)
            return
        self.status.set('安装包已生成：' + output)
        messagebox.showinfo('安装包已生成', '把这个新电脑安装包复制到各台新电脑即可。\n'
                            '每台电脑仍需根据设备码单独激活。安装包没有授权私钥、使用记录或已激活状态。', parent=self.root)

    def generate(self):
        self.code.configure(state='normal')
        self.code.delete('1.0', 'end')
        self.code.configure(state='disabled')
        try:
            code = issue_activation(BASE, self.device.get('1.0', 'end'))
        except ActivationError as error:
            self.status.set(str(error))
            return
        self.code.configure(state='normal')
        self.code.insert('1.0', code)
        self.code.configure(state='disabled')
        self.status.set('已生成本设备专属激活码，请点击“复制激活码”后发给使用者。')

    def copy(self):
        code = self.code.get('1.0', 'end').strip()
        if not code:
            self.status.set('请先生成激活码。')
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        self.status.set('激活码已复制。')


def main():
    root = tk.Tk()
    OwnerTool(root)
    root.mainloop()


if __name__ == '__main__':
    main()

