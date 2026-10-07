"""Owner-only signing and clean installer provisioning. Never bundled in client."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import zipfile

from activation_codec import (ActivationError, make_code, parse_device,
                              public_key_from_json, signing_payload, verify_code)

TEMPLATE_FORMAT = 'pinganxile-installation-template-v1'
INSTALL_FORMAT = 'pinganxile-clean-installation-v1'


def run_rsa(mode, key_path, payload=None, executable=None):
    if executable is None:
        if os.name != 'nt':
            raise ActivationError('授权工具需要 Windows 自带的 Windows PowerShell 5.1。')
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    command = [str(executable), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(Path(__file__).with_name('owner_rsa.ps1')),
               '-Mode', mode, '-KeyPath', str(Path(key_path).resolve())]
    if payload is not None:
        command += ['-PayloadBase64', base64.b64encode(payload).decode('ascii')]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=40,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            raise ActivationError('Windows 授权签名操作失败。请确认原授权密钥完整，以及 Windows PowerShell 可正常运行；密钥不会被自动替换。')
        value = json.loads(result.stdout.lstrip('\ufeff').strip())
        public_key_from_json(value)
        return value
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        if isinstance(error, ActivationError):
            raise
        raise ActivationError('授权工具未能完成签名操作，请检查 Windows PowerShell 环境。') from error


def key_path(base):
    return Path(base) / '授权密钥' / 'owner_private_key.xml'


def authority_public(base):
    key = key_path(base)
    if not key.is_file():
        raise ActivationError('尚未初始化授权密钥。请先点击“首次初始化授权工具”；已使用过的工具请恢复原授权密钥备份。')
    value = run_rsa('public', key)
    return {name: value[name] for name in ('format', 'modulus', 'exponent')}


def initialize_authority(base):
    key = key_path(base)
    marker = key.parent / 'authority_initialized.json'
    if key.exists():
        return authority_public(base)
    if marker.exists():
        raise ActivationError('检测到旧授权记录，但原密钥缺失。请恢复“授权密钥”文件夹备份，不要重新初始化。')
    key.parent.mkdir(parents=True, exist_ok=True)
    value = run_rsa('generate', key)
    public = {name: value[name] for name in ('format', 'modulus', 'exponent')}
    marker.write_text(json.dumps(public, indent=2) + '\n', encoding='utf-8')
    return public


def issue_activation(base, device_text):
    device = parse_device(device_text)
    public = authority_public(base)
    result = run_rsa('sign', key_path(base), signing_payload(device))
    try:
        signature = base64.b64decode(result['signature'], validate=True)
    except (KeyError, ValueError, TypeError) as error:
        raise ActivationError('签发结果无效，未生成激活码。') from error
    code = make_code(device, signature)
    verify_code(code, device, public_key_from_json(public))
    return code


def build_installation(template_path, output_path, public):
    """Copy a known clean template, add the public key, exclude signing material."""
    public_key_from_json(public)
    template_path, output_path = Path(template_path), Path(output_path)
    if template_path.resolve() == output_path.resolve():
        raise ActivationError('输出安装包不能覆盖原始模板。')
    if output_path.exists():
        raise ActivationError('输出文件已存在，请选择新的文件名；已有安装包不会被覆盖。')
    temporary = output_path.with_name(output_path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with zipfile.ZipFile(template_path) as source:
            items = source.infolist()
            names = [item.filename for item in items if not item.is_dir()]
            if len(names) != len(set(names)) or len(names) > 200 or sum(x.file_size for x in items) > 150_000_000:
                raise ActivationError('安装模板内容无效。')
            markers = [name for name in names if name.endswith('/activation_template.json')]
            if len(markers) != 1:
                raise ActivationError('请选择下载的“新电脑安装模板.zip”，不能选择旧程序或普通补丁。')
            marker_name = markers[0]
            root = str(PurePosixPath(marker_name).parent)
            marker = json.loads(source.read(marker_name))
            if marker.get('format') != TEMPLATE_FORMAT or marker.get('version') != '0.6.117':
                raise ActivationError('安装模板版本或格式不匹配。')
            manifest_name = root + '/installation_manifest.json'
            manifest = json.loads(source.read(manifest_name))
            if manifest.get('format') != INSTALL_FORMAT or not isinstance(manifest.get('files'), dict):
                raise ActivationError('安装模板缺少文件校验清单。')
            files = manifest['files']
            expected_names = {root + '/' + name for name in files} | {marker_name, manifest_name}
            if set(names) != expected_names:
                raise ActivationError('安装模板包含未经列入清单的文件，请重新下载模板。')
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name or len(path.parts) < 2 or path.parts[0] != root:
                    raise ActivationError('安装模板路径无效。')
                leaf = path.name.lower()
                if (leaf.endswith(('.sqlite3', '.sqlite', '.db', '.csv', '.log', '.xml'))
                        or leaf in ('activation.json', 'activation_public_key.json')
                        or 'owner_private' in leaf or '授权密钥' in path.parts):
                    raise ActivationError('安装模板包含使用记录或授权私钥，不允许分发。')
            public_bytes = (json.dumps(public, indent=2) + '\n').encode('utf-8')
            finalized = dict(manifest)
            finalized['files'] = dict(files)
            finalized['files']['activation_public_key.json'] = hashlib.sha256(public_bytes).hexdigest()
            finalized['requires_owner_provisioning'] = False
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as destination:
                for name in names:
                    if name in (marker_name, manifest_name):
                        continue
                    raw = source.read(name)
                    relative = name[len(root) + 1:]
                    if hashlib.sha256(raw).hexdigest() != files[relative]:
                        raise ActivationError('安装模板文件校验失败：' + relative)
                    destination.writestr(name, raw)
                destination.writestr(root + '/activation_public_key.json', public_bytes)
                destination.writestr(manifest_name, json.dumps(finalized, ensure_ascii=False, indent=2) + '\n')
        # Do not overwrite an installation produced by another open tool window.
        if output_path.exists():
            raise ActivationError('输出文件已在其他窗口创建，请选择新的文件名。')
        os.replace(temporary, output_path)
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, TypeError) as error:
        if isinstance(error, ActivationError):
            raise
        raise ActivationError('安装包制作失败，请检查模板和保存位置；原始模板及授权密钥不会被修改。') from error
    finally:
        temporary.unlink(missing_ok=True)
    return output_path

