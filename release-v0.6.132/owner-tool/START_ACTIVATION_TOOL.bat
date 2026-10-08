@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 activation_owner.py
if errorlevel 1 goto failed
exit /b 0
:usepython
where python >nul 2>nul
if errorlevel 1 goto missing
python activation_owner.py
if errorlevel 1 goto failed
exit /b 0
:missing
echo Python was not found. Install the same Python used for the assistant.
pause
exit /b 1
:failed
echo Activation tool did not start correctly. Keep this window open and send the error text.
pause
exit /b 1

