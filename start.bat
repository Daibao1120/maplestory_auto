@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe goto noenv
set PYTHONUTF8=1
chcp 65001 >nul
:run
.venv\Scripts\python.exe run.py
rem exit code 3 = restart command (reload new code)
if %errorlevel%==3 goto run
pause
exit /b 0
:noenv
echo Please run install.bat first.
pause
