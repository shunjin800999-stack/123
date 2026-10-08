"""Owner-side shortcut for the unchanged v132 installer and activation issuer."""
import hashlib
import json
import os
from pathlib import Path
import queue
import secrets
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from activation_codec import ActivationError, parse_device, public_key_from_json
import issuer


BASE = Path(__file__).resolve().parent
TEMPLATE = Path('安装材料') / '新电脑安装模板.zip'
SETTINGS = '准备工具设置.json'
OUTPUT = '生成的新电脑安装包'
INSTALLER = '平安喜乐助手-v0.6.132-新电脑安装包.zip'
GUIDE = '新电脑简明步骤.txt'


def fingerprint(public):
    modulus, exponent = public_key_from_json(public)
    raw = str(modulus).encode('ascii') + b':' + str(exponent).encode('ascii')
    return hashlib.sha256(raw).hexdigest()


class Preparation:
    def __init__(self, base=BASE, output_root=None):
        self.base = Path(base).resolve()
        self.output_directory = (Path(output_root).resolve() if output_root is not None else self.base) / OUTPUT

    def _selection(self):
        settings = self.base / SETTINGS
        if settings.exists():
            try:
                value = json.loads(settings.read_text(encoding='utf-8'))
                directory, identity = value['authority_directory'], value['authority_fingerprint']
                if (not isinstance(directory, str) or not directory
                        or not isinstance(identity, str) or len(identity) != 64
                        or any(char not in '0123456789abcdef' for char in identity)):
                    raise ValueError('Invalid saved authority')
                path = self.base if directory == '.' else Path(directory)
                if not path.is_absolute():
                    raise ValueError('Invalid saved authority path')
                return path, identity
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ActivationError('准备工具设置读取失败。请恢复备份中的“准备工具设置.json”和“授权密钥”，不要重新建立授权。') from error
        local_key = issuer.key_path(self.base)
        if local_key.exists() or (local_key.parent / 'authority_initialized.json').exists():
            return self.base, None
        return None, None

    def configured(self):
        return ((self.base / SETTINGS).exists() or issuer.key_path(self.base).exists()
                or (issuer.key_path(self.base).parent / 'authority_initialized.json').exists())

    def _remember(self, directory, public):
        directory = Path(directory).resolve()
        value = {'authority_directory': '.' if directory == self.base else str(directory),
                 'authority_fingerprint': fingerprint(public)}
        settings = self.base / SETTINGS
        temporary = settings.with_name(settings.name + '.' + secrets.token_hex(8) + '.tmp')
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            os.replace(temporary, settings)
        finally:
            temporary.unlink(missing_ok=True)

    def public(self):
        directory, identity = self._selection()
        if directory is None:
            raise ActivationError('请先制作新电脑安装包，首次使用时选择是否用过激活码工具。')
        if not issuer.key_path(directory).is_file():
            raise ActivationError('原授权文件找不到。请恢复原工具目录中的“授权密钥”备份；工具不会建立另一套授权。')
        public = issuer.authority_public(directory)
        if identity is not None and identity != fingerprint(public):
            raise ActivationError('原授权文件发生变化。请恢复原“授权密钥”备份，已有安装包仍需使用原授权。')
        if identity is None:
            self._remember(directory, public)
        return public

    def first_use(self):
        if self.configured():
            return self.public()
        public = issuer.initialize_authority(self.base)
        self._remember(self.base, public)
        return public

    def use_existing(self, directory):
        directory = Path(directory).resolve()
        if directory.name == '授权密钥':
            directory = directory.parent
        if not issuer.key_path(directory).is_file():
            raise ActivationError('所选文件夹没有原授权文件。请选择里面有“授权密钥”的原激活码工具目录。')
        public = issuer.authority_public(directory)
        if self.configured():
            current, identity = self._selection()
            if identity is None:
                identity = fingerprint(issuer.authority_public(current))
            if identity != fingerprint(public):
                raise ActivationError('这个准备包已使用另一套授权。请选择原工具目录，避免已有安装包无法激活。')
        self._remember(directory, public)
        return public

    def prepare_installation(self):
        public = self.public()
        template = self.base / TEMPLATE
        if not template.is_file():
            raise ActivationError('安装材料缺失。请把整个简易准备包解压后再打开，不要只解压启动文件。')
        guide = (self.base / GUIDE).read_bytes()
        directory = self.output_directory
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / INSTALLER
        index = 2
        while output.exists():
            output = directory / (Path(INSTALLER).stem + '-' + str(index) + '.zip')
            index += 1
        issuer.build_installation(template, output, public)
        (directory / GUIDE).write_bytes(guide)
        return output

    def generate(self, device_text):
        parse_device(device_text)
        self.public()  # Enforce the remembered signing authority before signing.
        directory, _ = self._selection()
        return issuer.issue_activation(directory, device_text)


class SimpleSetup:
    def __init__(self, root, preparation=None):
        self.root = root
        self.preparation = preparation or Preparation(BASE, BASE.parent if BASE.name == '工具文件' else BASE)
        self.messages = queue.Queue()
        self.busy_buttons = []
        root.title('平安喜乐助手 v0.6.132 · 新电脑简易准备')
        root.geometry('760x660')
        root.minsize(680, 640)
        try:
            root.iconbitmap(default=str(BASE / 'gold_ingot.ico'))
        except tk.TclError:
            pass
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='在你现在这台电脑操作', font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='工具和模板已经放在一起，按下面两步使用。').pack(anchor='w', pady=(6, 18))
        step1 = ttk.LabelFrame(frame, text='① 制作安装包', padding=12)
        step1.pack(fill='x')
        self._button(step1, '制作新电脑安装包', self.package).pack(anchor='w')
        ttk.Label(step1, text='制作完成后自动打开文件夹。把里面的安装包和“新电脑简明步骤.txt”复制到新电脑。',
                  wraplength=650).pack(anchor='w', pady=(8, 6))
        ttk.Button(step1, text='打开生成的安装包文件夹', command=self.open_output).pack(anchor='w')
        step2 = ttk.LabelFrame(frame, text='② 新电脑显示设备码后，回到这里生成激活码', padding=12)
        step2.pack(fill='x', pady=(18, 10))
        ttk.Label(step2, text='粘贴新电脑复制的设备码（PA1- 开头）：').pack(anchor='w')
        self.device = tk.Text(step2, height=3, wrap='char')
        self.device.pack(fill='x', pady=6)
        self._button(step2, '生成并复制激活码', self.generate).pack(anchor='w', pady=6)
        ttk.Label(step2, text='生成后直接在新电脑激活窗口粘贴，之后不用再输入。').pack(anchor='w')
        self.code = tk.Text(step2, height=4, wrap='char', state='disabled')
        self.code.pack(fill='x', pady=6)
        ttk.Button(step2, text='再次复制激活码', command=self.copy).pack(anchor='w')
        self.status = tk.StringVar(root, value='点击“制作新电脑安装包”开始。')
        ttk.Label(frame, textvariable=self.status, wraplength=680, foreground='#9b3600').pack(anchor='w', pady=8)
        ttk.Label(frame, text='备份整个准备包。工具和“授权密钥”由你保管，只把生成的安装包给新电脑。',
                  wraplength=680).pack(anchor='w', side='bottom')

    def _button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        self.busy_buttons.append(button)
        return button

    def _initial_choice(self):
        if self.preparation.configured():
            return ('ready', None)
        dialog = tk.Toplevel(self.root)
        dialog.title('第一次准备')
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='你以前生成过激活码吗？', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='只选择一次，之后会自动记住。').pack(anchor='w', pady=(6, 14))
        result = []

        def first():
            result.append(('first', None))
            dialog.destroy()

        def existing():
            directory = filedialog.askdirectory(parent=dialog, title='选择原激活码工具文件夹（里面有“授权密钥”）', mustexist=True)
            if directory:
                result.append(('existing', directory))
                dialog.destroy()

        ttk.Button(frame, text='没有，第一次使用', command=first).pack(fill='x', pady=4)
        ttk.Button(frame, text='有，选择原激活码工具文件夹', command=existing).pack(fill='x', pady=4)
        ttk.Label(frame, text='用过旧工具的请选择原目录，原激活码会继续有效。', wraplength=450).pack(anchor='w', pady=(10, 0))
        dialog.grab_set()
        self.root.wait_window(dialog)
        return result[0] if result else None

    def _apply_choice(self, choice):
        mode, directory = choice
        if mode == 'first':
            self.preparation.first_use()
        elif mode == 'existing':
            self.preparation.use_existing(directory)

    def _run(self, operation, success, status):
        for button in self.busy_buttons:
            button.state(['disabled'])
        self.status.set(status)

        def worker():
            try:
                self.messages.put((True, operation()))
            except Exception as error:
                self.messages.put((False, str(error)))

        def poll():
            try:
                passed, value = self.messages.get_nowait()
            except queue.Empty:
                self.root.after(100, poll)
                return
            for button in self.busy_buttons:
                button.state(['!disabled'])
            if passed:
                success(value)
            else:
                self.status.set(value)
                messagebox.showerror('这一步未完成', value, parent=self.root)

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, poll)

    def package(self):
        choice = self._initial_choice()
        if choice is None:
            return

        def operation():
            self._apply_choice(choice)
            return self.preparation.prepare_installation()

        def success(output):
            self.status.set('已生成：' + output.name + '。把它和简明步骤复制到新电脑即可。')
            self.open_output()

        self._run(operation, success, '正在制作安装包，请稍候……')

    def generate(self):
        device = self.device.get('1.0', 'end').strip()
        self._set_code('')
        try:
            parse_device(device)
        except ActivationError as error:
            messagebox.showerror('设备码未填写正确', str(error), parent=self.root)
            return
        choice = self._initial_choice()
        if choice is None:
            return

        def operation():
            self._apply_choice(choice)
            return self.preparation.generate(device)

        def success(code):
            self._set_code(code)
            self.copy()

        self._run(operation, success, '正在生成这台新电脑的激活码……')

    def _set_code(self, code):
        self.code.configure(state='normal')
        self.code.delete('1.0', 'end')
        self.code.insert('1.0', code)
        self.code.configure(state='disabled')

    def copy(self):
        code = self.code.get('1.0', 'end').strip()
        if not code:
            self.status.set('先粘贴新电脑的设备码，再点击“生成并复制激活码”。')
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(code)
        except tk.TclError:
            self.status.set('激活码已生成，但复制未完成。请从上方手动复制完整激活码。')
            return
        self.status.set('激活码已复制。请在对应新电脑的激活窗口粘贴；只需首次输入。')

    def open_output(self):
        directory = self.preparation.output_directory
        if not directory.is_dir():
            self.status.set('请先点击“制作新电脑安装包”。')
            return
        try:
            os.startfile(directory)
        except (OSError, AttributeError):
            self.status.set('安装包保存在：' + str(directory))


def main():
    root = tk.Tk()
    SimpleSetup(root)
    root.mainloop()


if __name__ == '__main__':
    main()
