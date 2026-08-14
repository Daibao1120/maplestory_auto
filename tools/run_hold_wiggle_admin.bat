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
REM   --move-time 0.18        seconds the arrow is HELD per step. Below ~0.1 the
REM                           character only turns to face and does NOT walk, so
REM                           it looks like it did not move. Raise this to walk more.
REM   --patrol-steps 2        how many steps to one side before turning back;
REM                           SMALLER for a smaller platform (try 1 if you fall off)
REM   --face left             starting attack direction ASSUMPTION. It does NOT
REM                           force-turn at launch (keeps your character's current
REM                           facing). When you manually switch sides in-game
REM                           (arrow to pause, turn, Ctrl to resume) it follows you.
REM   --interval-min 40       shortest gap between patrol steps (seconds).
REM   --interval-max 55       Attacks go stale after ~60s standing still, so it
REM                           repositions once before that. No need to move often.
REM   --no-refocus            DEFAULT here. When you click away to another window
REM                           (e.g. to type), it pauses and does NOT steal focus
REM                           or send any keys into your window. Click back on
REM                           MapleStory and it auto-resumes. REMOVE this flag if
REM                           you instead want it to grab focus back on popups.
REM   --no-move               add this to NOT move at all, just keep attacking
"C:\Users\ibuzz\anaconda3\envs\linebot_RAG\python.exe" tools\hold_and_wiggle.py --key ctrl --attack-interval 0.22 --move-time 0.18 --patrol-steps 2 --face left --interval-min 40 --interval-max 55 --no-refocus

echo.
echo Finished. Press any key to close.
pause >nul
