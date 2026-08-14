@echo off
REM Launch hold_and_wiggle as Administrator so injected keys reach the game.
REM The game runs elevated; without admin, Windows (UIPI) silently drops our keys.
REM Double-click this file, accept the UAC prompt.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "D:\indexasia_David\maplestory-classic-bot"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

REM Edit the arguments below to taste:
REM   --key ctrl              attack key, tapped repeatedly (default ctrl)
REM   --attack-interval 0.22  seconds between attack taps (smaller = faster)
REM   --move-time 0.05        seconds walked per step (smaller = smaller step)
REM   --patrol-steps 3        how many steps to one side before turning back;
REM                           SMALLER for a smaller platform (try 2 if you fall off)
REM   --interval-min 5        shortest gap between patrol steps (seconds)
REM   --interval-max 12       longest gap; each wait is randomized in this range
REM   --no-move               add this to NOT move at all, just keep attacking
"C:\Users\ibuzz\anaconda3\envs\linebot_RAG\python.exe" tools\hold_and_wiggle.py --key ctrl --attack-interval 0.22 --move-time 0.05 --patrol-steps 3 --interval-min 5 --interval-max 12

echo.
echo Finished. Press any key to close.
pause >nul
