@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe goto noenv
set PYTHONUTF8=1
chcp 65001 >nul
set AT=98
set /p AT=Stop auto-hunt at EXP percent [Enter = 98]: 
.venv\Scripts\python.exe autostop.py --at %AT%
pause
exit /b 0
:noenv
echo Please run install.bat first.
pause
