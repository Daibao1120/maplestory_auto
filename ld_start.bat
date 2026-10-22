@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe goto noenv
set PYTHONUTF8=1
chcp 65001 >nul
.venv\Scripts\python.exe run_ld.py
pause
exit /b 0
:noenv
echo Please run install.bat first.
pause
