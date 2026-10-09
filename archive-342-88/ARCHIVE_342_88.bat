@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Close the assistant before running this tool.
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 archive_before_cutoff.py
goto done
:usepython
python archive_before_cutoff.py
:done
pause
