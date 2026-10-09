"""Create the convenience ZIP without generating or packaging any authority."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

SOURCE = Path(__file__).resolve().parent
REPOSITORY = SOURCE.parents[1]
TEMPLATE_SHA256 = 'fc999b2c16e2153480f0ef91d208b449208b2dc9c7de4a1478debc5fdb0d6c26'


def build():
    template = REPOSITORY / 'TelegramAssistant-v0.6.132-new-pc-template.zip'
    if hashlib.sha256(template.read_bytes()).hexdigest() != TEMPLATE_SHA256:
        raise ValueError('The approved v132 template checksum does not match')
    for name in ('issuer.py', 'activation_codec.py', 'owner_rsa.ps1', 'gold_ingot.ico', 'gold_ingot.png'):
        if (SOURCE / name).read_bytes() != (SOURCE.parent / 'owner-tool' / name).read_bytes():
            raise ValueError('Original issuer files must be unchanged: ' + name)
    ast.parse((SOURCE / 'easy_setup.py').read_text(encoding='utf-8'), feature_version=(3, 11))
    output = REPOSITORY / 'TelegramAssistant-v0.6.132-easy-setup.zip'
    prefix = '平安喜乐助手-v0.6.132-简易准备包/'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ('开始准备新电脑.bat', '先看这个.txt'):
            archive.write(SOURCE / name, prefix + name)
        for name in ('easy_setup.py', 'issuer.py', 'activation_codec.py', 'owner_rsa.ps1',
                     'gold_ingot.ico', 'gold_ingot.png', '新电脑简明步骤.txt'):
            archive.write(SOURCE / name, prefix + '工具文件/' + name)
        archive.write(template, prefix + '工具文件/安装材料/新电脑安装模板.zip', compress_type=zipfile.ZIP_STORED)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != 10:
            raise ValueError('Invalid package')
    return {'file': output.name, 'entries': 10, 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    print(json.dumps(build(), indent=2))
