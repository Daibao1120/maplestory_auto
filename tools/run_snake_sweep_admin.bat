@echo off
REM Snake farming on a wide ground (first-floor) platform: sweep left/right and
REM attack in the direction of travel. Press Ctrl to START; press an arrow key to
REM take control back (pause); press Ctrl to resume; F12 to quit.
REM Runs as Administrator so injected keys reach the game (UIPI).

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "D:\indexasia_David\maplestory-classic-bot"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

REM Tune these to your platform:
REM   --attack-interval 0.10  seconds between attack taps (smaller = faster)
REM   --sweep-steps 10        steps to one side before turning back. BIGGER for a
REM                           wider platform; smaller if it walks off the ends.
REM   --step-interval 0.45    seconds between walk steps (attacks fire in between)
REM   --move-time 0.25        how far each step walks (bigger = covers more ground)
REM   --start-paused          waits for you to press Ctrl before it begins
REM   --no-refocus            pauses (does not steal focus) when you click away
"C:\Users\ibuzz\anaconda3\envs\linebot_RAG\python.exe" tools\hold_and_wiggle.py --key ctrl --attack-interval 0.10 --sweep --sweep-steps 10 --step-interval 0.45 --move-time 0.25 --start-paused --no-refocus

echo.
echo Finished. Press any key to close.
pause >nul
