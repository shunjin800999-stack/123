"""Install optional OCR dependencies in this folder's own environment."""
from pathlib import Path
import os
import struct
import subprocess
import sys
import venv

base=Path(__file__).resolve().parent
log_path=base/'ocr_setup.log'
with log_path.open('w',encoding='utf-8') as log:
    def say(text):
        print(text,flush=True);log.write(text+'\n');log.flush()
    try:
        if os.name!='nt' or struct.calcsize('P')!=8 or not (3,11)<=sys.version_info[:2]<=(3,14):
            raise RuntimeError('本测试需要 Windows 上的 Python 3.11–3.14（64 位）。请反馈此日志，不用重装其他软件。')
        say('正在准备本文件夹的备用识别环境，不修改其他 Python 环境。')
        env=base/'.ocr-env'
        venv.EnvBuilder(with_pip=True).create(env)
        python=env/'Scripts'/'python.exe'
        command=[str(python),'-m','pip','install','--disable-pip-version-check','--prefer-binary',
            '-r',str(base/'requirements_ocr.txt')]
        with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,encoding='utf-8',errors='replace') as process:
            for line in process.stdout:say(line.rstrip())
            if process.wait():raise RuntimeError('依赖安装未完成。')
        check=subprocess.run([str(python),'-c','import rapidocr,onnxruntime,PIL; print("OCR imports OK")'],
            capture_output=True,text=True,encoding='utf-8',errors='replace')
        say(check.stdout+check.stderr)
        if check.returncode:raise RuntimeError('识别组件尚未加载成功。')
        say('安装成功。现在运行 START.bat，使用“备用文字识别测试（不点击）”。')
    except Exception as error:
        say(str(error));say('请反馈 ocr_setup.log；先不要继续运行选人测试。')
        sys.exit(1)
