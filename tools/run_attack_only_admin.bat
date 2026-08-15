@echo off
REM ============================================================
REM  ATTACK-ONLY launcher (frozen preset -- user's favorite).
REM  The tool ONLY:
REM    - holds the attack key down (re-pressed every 2-4s so the
REM      game never drops the held state)
REM    - auto right-clicks away unwanted buffs (speed boosts) if
REM      icon screenshots exist in assets\templates\buffs\
REM  It NEVER moves or turns your character. You do all movement:
REM  arrow keys pause it instantly, tap Ctrl to resume attacking.
REM  Reminder: standing still ~60s stales attacks -- step a little
REM  yourself once in a while. F12 quits.
REM  This file is a stable preset; run_hold_wiggle_admin.bat is the
REM  configurable one that may change over time.
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

REM ---- Park this console as a tall strip on the RIGHT of the game window ----
set GAME_WIDTH=2732
powershell -NoProfile -Command "$d='[DllImport(\"user32.dll\")]public static extern bool SetProcessDPIAware();[DllImport(\"kernel32.dll\")]public static extern System.IntPtr GetConsoleWindow();[DllImport(\"user32.dll\")]public static extern bool MoveWindow(System.IntPtr h,int x,int y,int w,int ht,bool r);[DllImport(\"user32.dll\")]public static extern int GetSystemMetrics(int i);';$W=Add-Type -MemberDefinition $d -Name Win -Namespace P -PassThru;[P.Win]::SetProcessDPIAware()|Out-Null;$sw=[P.Win]::GetSystemMetrics(0);$sh=[P.Win]::GetSystemMetrics(1);$w=[Math]::Max(400,$sw-%GAME_WIDTH%);[P.Win]::MoveWindow([P.Win]::GetConsoleWindow(),$sw-$w,0,$w,$sh,$true)|Out-Null"

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

%PYCMD% tools\hold_and_wiggle.py --key ctrl --hold-attack --no-move --dispel-buff --face left --fixed-face --no-smart-face --no-refocus

echo.
echo Finished. Press any key to close.
pause >nul
exit /b

:probe
if defined PYCMD exit /b
py -%1 -c "" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b
