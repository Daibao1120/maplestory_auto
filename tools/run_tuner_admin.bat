@echo off
REM Launch the tuning UI as Administrator (reading key state while the elevated
REM game window has focus is blocked by UIPI for non-admin processes).
REM Double-click this file, accept the UAC prompt.

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

REM ---- Pick a Python 3.8+: project .venv first, then the py launcher, then PATH.
set "PYCMD="
if exist ".venv\Scripts\python.exe" set "PYCMD=.venv\Scripts\python.exe"
if not defined PYCMD for %%V in (3.13 3.12 3.11 3.10 3.9 3.8) do call :probe %%V
if not defined PYCMD (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
    echo [ERROR] No Python 3.8+ found. Install Python 3.10+ or create the project
    echo         venv first:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
echo Using Python: %PYCMD%

%PYCMD% tools\tuner.py

echo.
echo Finished. Press any key to close.
pause >nul
exit /b

:probe
if defined PYCMD exit /b
py -%1 -c "" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b
