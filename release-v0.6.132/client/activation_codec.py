"""Offline device licenses: RSA-2048 / PKCS#1 v1.5 / SHA-256 verification.

The client contains public verification data only. No signing key, database,
network request, or third-party Python dependency is used here.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets

PUBLIC_FORMAT = 'pinganxile-public-key-v1'
RECORD_FORMAT = 'pinganxile-activation-v1'
DOMAIN = b'pinganxile-activation-v1\x00'
SHA256_DER_PREFIX = bytes.fromhex('3031300d060960864801650304020105000420')


class ActivationError(ValueError):
    pass


def device_id():
    if os.name != 'nt':
        raise ActivationError('设备码只能在 Windows 电脑上读取。')
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Cryptography', 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            guid, _ = winreg.QueryValueEx(key, 'MachineGuid')
        if not isinstance(guid, str) or not guid.strip() or len(guid) > 256:
            raise ValueError('invalid machine identifier')
        return hashlib.sha256(('pinganxile-device-v1:' + guid.strip().lower()).encode()).digest()
    except (OSError, ValueError) as error:
        raise ActivationError('无法读取本机设备码，请检查 Windows 环境；授权验证不会被跳过。') from error


def display_device(device):
    if not isinstance(device, bytes) or len(device) != 32:
        raise ActivationError('设备码格式无效。')
    value = device.hex().upper()
    return 'PA1-' + '-'.join(value[i:i + 8] for i in range(0, 64, 8))


def parse_device(value):
    if not isinstance(value, str) or len(value) > 256:
        raise ActivationError('请粘贴完整的 PA1- 开头设备码。')
    value = ''.join(value.split()).upper()
    if not value.startswith('PA1-'):
        raise ActivationError('请粘贴完整的 PA1- 开头设备码。')
    value = value[4:].replace('-', '')
    if not re.fullmatch(r'[0-9A-F]{64}', value):
        raise ActivationError('设备码不完整，请重新复制。')
    return bytes.fromhex(value)


def signing_payload(device):
    display_device(device)
    return DOMAIN + device


def _b64encode(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _b64decode(value, length):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ActivationError('激活码格式无效，请重新复制完整激活码。')
    try:
        decoded = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except (ValueError, TypeError) as error:
        raise ActivationError('激活码格式无效。') from error
    if len(decoded) != length or _b64encode(decoded) != value:
        raise ActivationError('激活码不完整，请重新复制。')
    return decoded


def make_code(device, signature):
    display_device(device)
    if not isinstance(signature, bytes) or len(signature) != 256:
        raise ActivationError('签发结果无效。')
    return 'PAK1.' + _b64encode(device) + '.' + _b64encode(signature)


def public_key_from_json(value):
    try:
        if not isinstance(value, dict) or value.get('format') != PUBLIC_FORMAT:
            raise ValueError('invalid key format')
        modulus = base64.b64decode(value['modulus'], validate=True)
        exponent = base64.b64decode(value['exponent'], validate=True)
        n, e = int.from_bytes(modulus, 'big'), int.from_bytes(exponent, 'big')
        if len(modulus) != 256 or n.bit_length() != 2048 or n % 2 != 1 or e != 65537:
            raise ValueError('invalid RSA parameters')
        return n, e
    except (KeyError, ValueError, TypeError) as error:
        raise ActivationError('授权校验文件无效，请使用本人授权工具制作的安装包。') from error


def load_public_key(path):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError('oversized key')
        value = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError, UnicodeError) as error:
        raise ActivationError('缺少或无法读取授权校验文件。请先用激活码工具的“制作新电脑安装包”生成正式安装包。') from error
    return public_key_from_json(value)


def verify_code(code, device, public_key):
    display_device(device)
    if not isinstance(code, str) or len(code) > 2048:
        raise ActivationError('激活码格式无效。')
    normalized = ''.join(code.split())
    fields = normalized.split('.')
    if len(fields) != 3 or fields[0] != 'PAK1':
        raise ActivationError('请粘贴完整的 PAK1. 开头激活码。')
    issued_device = _b64decode(fields[1], 32)
    signature = _b64decode(fields[2], 256)
    if not hmac.compare_digest(issued_device, device):
        raise ActivationError('这个激活码不属于当前电脑，请将本机设备码发给授权人。')
    n, e = public_key
    signature_number = int.from_bytes(signature, 'big')
    if signature_number >= n:
        raise ActivationError('激活码验证失败，请联系授权人重新生成。')
    encoded = pow(signature_number, e, n).to_bytes(256, 'big')
    digest_info = SHA256_DER_PREFIX + hashlib.sha256(signing_payload(device)).digest()
    expected = b'\x00\x01' + b'\xff' * (256 - len(digest_info) - 3) + b'\x00' + digest_info
    if not hmac.compare_digest(encoded, expected):
        raise ActivationError('激活码验证失败，请使用本安装包对应的授权工具生成。')
    return normalized


def saved_activation_valid(path, device, public_key):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            return False
        value = json.loads(raw.decode('utf-8'))
        if not isinstance(value, dict) or value.get('format') != RECORD_FORMAT:
            return False
        verify_code(value.get('code'), device, public_key)
        return True
    except (OSError, ValueError, UnicodeError, TypeError):
        return False


def save_activation(path, code, device, public_key):
    normalized = verify_code(code, device, public_key)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump({'format': RECORD_FORMAT, 'code': normalized}, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

