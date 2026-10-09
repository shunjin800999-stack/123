@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 app.py
if errorlevel 1 goto failed
exit /b 0
:usepython
where python >nul 2>nul
if errorlevel 1 goto missing
python app.py
if errorlevel 1 goto failed
exit /b 0
:missing
echo Python was not found. Install Python from https://www.python.org/downloads/windows/
echo Include tkinter support and the Python launcher or add Python to PATH.
pause
exit /b 1
:failed
echo The application did not start correctly. Keep this window open and send the error text.
pause
exit /b 1
