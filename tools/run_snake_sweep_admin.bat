@echo off
REM Snake farming on a wide ground platform: sweep left/right and attack in the
REM direction of travel. Press Ctrl to START; press an arrow key to take control
REM back (pause); press Ctrl to resume; F12 to quit.
REM Runs as Administrator so injected keys reach the game (UIPI).

REM Admin probe: fltmc needs admin but NOT the LanmanServer service ("net session"
REM fails on machines with the Server service disabled even when elevated, which
REM would make this script relaunch itself forever).
fltmc >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath \"%~f0\" -Verb RunAs"
    exit /b
)

REM Repo root = parent folder of this tools\ directory (no hard-coded paths, so
REM it works from wherever the repo was cloned).
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

REM Tune these to your platform:
REM   --attack-interval 0.10  seconds between attack taps (smaller = faster)
REM   --sweep-steps 10        steps to one side before turning back. BIGGER for a
REM                           wider platform; smaller if it walks off the ends.
REM   --step-interval 0.45    seconds between walk steps (attacks fire in between)
REM   --move-time 0.25        how far each step walks (bigger = covers more ground)
REM   --start-paused          waits for you to press Ctrl before it begins, so you
REM                           can stand where you want first
REM   --edge-guard            reads your REAL position on the minimap and turns
REM                           back early if the next step would leave the safe
REM                           range. Blind step-counting drifts after knockback or
REM                           on slopes -- that drift is what walks you off the end.
REM   --edge-margin 40        safe range = start x +/- this many minimap pixels.
REM                           Your measured platform is ~107 wide, so 40 keeps a
REM                           healthy buffer. SMALLER for a narrower platform.
REM   --adaptive-sweep        watches which side has movement (monsters move, the
REM                           background does not): lingers to finish off what is
REM                           in front, walks faster when the area is cleared, and
REM                           turns back early when the monsters are behind you.
REM                           Only changes TIMING -- it never flips your facing
REM                           while standing still.
REM   --dispel-buff           right-clicks away speed boosts (they make every step
REM                           overshoot and walk you off the platform)
REM   --no-refocus            pauses (does not steal focus) when you click away
%PYCMD% tools\hold_and_wiggle.py --key ctrl --attack-interval 0.10 --sweep --sweep-steps 10 --step-interval 0.45 --move-time 0.25 --start-paused --edge-guard --edge-margin 40 --adaptive-sweep --dispel-buff --no-refocus

echo.
echo Finished. Press any key to close.
pause >nul
exit /b

:probe
if defined PYCMD exit /b
py -%1 -c "" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b
