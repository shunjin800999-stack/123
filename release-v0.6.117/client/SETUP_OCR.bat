@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 setup_ocr.py
goto done
:usepython
python setup_ocr.py
:done
pause
