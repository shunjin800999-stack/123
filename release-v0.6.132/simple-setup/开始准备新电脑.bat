@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 "工具文件\easy_setup.py"
if errorlevel 1 goto failed
exit /b 0
:usepython
where python >nul 2>nul
if errorlevel 1 goto missing
python "工具文件\easy_setup.py"
if errorlevel 1 goto failed
exit /b 0
:missing
echo Python was not found. Use this tool on the computer where the assistant already runs.
echo Otherwise install Python 3.13 x64 from https://www.python.org/downloads/windows/
pause
exit /b 1
:failed
echo The tool could not start. Keep this window open and send the error text.
pause
exit /b 1
