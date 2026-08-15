@echo off
REM Launch hold_and_wiggle as Administrator so injected keys reach the game.
REM The game runs elevated; without admin, Windows (UIPI) silently drops our keys.
REM Double-click this file, accept the UAC prompt.

REM Admin probe: fltmc needs admin but NOT the LanmanServer service ("net session"
REM fails on machines with the Server service disabled even when elevated, which
REM would make this script relaunch itself forever).
fltmc >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath \"%~f0\" -Verb RunAs"
    exit /b
)

REM Repo root = parent folder of this tools\ directory (no hard-coded paths,
REM works from wherever the repo was cloned/unzipped). pushd also maps UNC
REM paths to a temp drive letter (cmd cannot cd to \\server\share directly).
pushd "%~dp0.." || (
    echo [ERROR] Cannot enter the repo folder. Run from a local or mapped drive.
    pause
    exit /b 1
)
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

REM ---- Park this console as a tall strip on the RIGHT of the game window ----
REM GAME_WIDTH = width of your MapleStory window in physical pixels; the strip
REM uses whatever screen width is left (min 400 px, full screen height), so no
REM manual resizing every launch. Adjust GAME_WIDTH if you resize the game.
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

REM Edit the arguments below to taste:
REM   --key ctrl              attack key, tapped repeatedly (default ctrl)
REM   --attack-interval 0.22  seconds between attack taps (smaller = faster)
REM   --move-time 0.18        seconds the arrow is HELD per step. Below ~0.1 the
REM                           character only turns to face and does NOT walk, so
REM                           it looks like it did not move. Raise this to walk more.
REM   --edge-guard            reads your real position on the MINIMAP and turns
REM                           back at the edge, so it never walks off the platform.
REM                           Needs opencv/mss (route B). Falls back to blind
REM                           patrol automatically if they are missing.
REM   --edge-margin 6         safe range = start x +/- this many minimap pixels.
REM                           SMALLER for a narrower platform.
REM   --patrol-steps 1        blind fallback only (used when --edge-guard is off /
REM                           unavailable): steps to one side before turning back.
REM   --face left             starting attack side assumption; it does NOT
REM                           force-turn at launch.
REM   --fixed-face            ON here: the tool NEVER switches sides on its own
REM                           (user preference: auto side-switching felt wrong).
REM                           YOU control the side: arrow key to pause, turn,
REM                           Ctrl to resume -- it then keeps attacking that
REM                           side. Remove this flag to let each patrol step
REM                           alternate sides again.
REM   --no-smart-face         ON here: motion-based "attack where monsters are"
REM                           disabled, per user preference. Remove to re-enable.
REM   --interval-min 40       shortest gap between patrol steps (seconds).
REM   --interval-max 55       Attacks go stale after ~60s standing still, so it
REM                           repositions once before that. No need to move often.
REM   --no-refocus            DEFAULT here. When you click away to another window
REM                           (e.g. to type), it pauses and does NOT steal focus
REM                           or send any keys into your window. Click back on
REM                           MapleStory and it auto-resumes. REMOVE this flag if
REM                           you instead want it to grab focus back on popups.
REM   --no-move               add this to NOT move at all, just keep attacking
REM   --dispel-buff           watch the top-right buff bar and RIGHT-CLICK away
REM                           unwanted buffs (e.g. speed boosts that make every
REM                           step overshoot and walk off the platform). Put a
REM                           screenshot of each unwanted buff icon into
REM                           assets\templates\buffs\ first; without templates
REM                           it just prints a hint and stays off.
REM   --shuffle               reposition with a tiny THERE-AND-BACK step: real
REM                           horizontal movement (the stale-attack check needs
REM                           horizontal displacement -- jumping in place does
REM                           NOT reset it), but net movement ~0 so narrow
REM                           branch platforms are safe. The outbound leg goes
REM                           toward the platform center first, and the return
REM                           leg is slightly shorter, so each shuffle nudges
REM                           you back toward center.
REM ---- CURRENT SETUP: NO automatic movement at all (user decision) ----
REM The tool ONLY: spams the attack key + auto right-clicks away unwanted buffs.
REM YOU handle all movement/repositioning yourself (arrow keys pause it, hold
REM Ctrl to resume attacking). Note: standing still ~60s stales attacks -- take
REM a small step yourself once in a while.
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
