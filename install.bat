@echo off
cd /d "%~dp0"
echo Looking for Python 3.10+ 64-bit ...
set PY=
for %%v in (3.12 3.13 3.11 3.10 3.14 3.15) do call :try %%v
if not defined PY goto nopy
echo Using: %PY%
if exist .venv rmdir /s /q .venv
%PY% -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo ==== INSTALL OK. Next: run calibrate.bat ====
pause
exit /b 0

:try
if defined PY exit /b 0
py -%1-64 -c "import sys" >nul 2>&1
if not errorlevel 1 set PY=py -%1-64
exit /b 0

:nopy
echo.
echo [ERROR] No Python 3.10+ 64-bit found.
echo Install from https://www.python.org/downloads/ and run this again.
pause
exit /b 1

:fail
echo.
echo [ERROR] Install failed. Copy the messages above and send them to Claude.
pause
exit /b 1
