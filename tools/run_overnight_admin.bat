@echo off
REM ============================================================
REM  Overnight leveling daemon (tools/overnight.py, NightWatchCore).
REM  Fully tested state machine: 119 unit tests + mutation testing.
REM  It refuses to farm unless it can PROVE healing works, and it
REM  auto-discovers which quickslot key holds your potion.
REM  Start the GAME first and log in. F12 quits. Arrow keys yield.
REM ============================================================

fltmc >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath \"%~f0\" -Verb RunAs"
    exit /b
)

pushd "%~dp0.." || (
    echo [ERROR] Cannot enter the repo folder. Run from a local or mapped drive.
    pause
    exit /b 1
)
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

set "PYCMD="
if exist ".venv\Scripts\python.exe" set "PYCMD=.venv\Scripts\python.exe"
if not defined PYCMD for %%V in (3.13 3.12 3.11 3.10 3.9 3.8) do call :probe %%V
if not defined PYCMD set "PYCMD=python"
echo Using Python: %PYCMD%

if not exist "logs" mkdir "logs"
%PYCMD% tools\overnight.py --run --dir logs --hours 7.5

echo.
echo Finished. Log: logs\daemon_log.txt
pause >nul
exit /b

:probe
if defined PYCMD exit /b
py -%1 -c "" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b
