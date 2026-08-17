@echo off
REM ============================================================
REM  Overnight CONTROL PANEL (tools/control_panel.py)
REM  Live preview of what the bot sees + status dashboard +
REM  live-tunable parameters + start/pause/stop buttons.
REM  Start the GAME first and log in, then run this.
REM  Tip: press the "only look" button first to confirm detection
REM  is correct, then "start leveling".
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

%PYCMD% tools\control_panel.py

echo.
echo Panel closed. Press any key.
pause >nul
exit /b

:probe
if defined PYCMD exit /b
py -%1 -c "" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b
