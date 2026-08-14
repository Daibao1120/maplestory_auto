@echo off
REM Launch send_ctrl_to_maple as Administrator so injected keys reach the game.
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

REM --method postmessage : send Ctrl to the Maple window in the BACKGROUND.
REM                        You can click other windows and it keeps sending.
REM                        BUT DirectInput games often ignore PostMessage -- if
REM                        the character does not react, switch to sendinput below.
REM --method sendinput   : steal focus back to Maple each time, then send.
REM                        Reliable while Maple is the active window, but stops
REM                        working the moment you click away.
REM --interval 0.8       : send Ctrl every 0.8 seconds
%PYCMD% tools\send_ctrl_to_maple.py --method postmessage --interval 0.8

echo.
echo Finished. Press any key to close.
pause >nul
exit /b

:probe
if defined PYCMD exit /b
py -%1 -c "" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b
