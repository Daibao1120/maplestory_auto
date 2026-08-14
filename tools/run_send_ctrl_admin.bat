@echo off
REM Launch send_ctrl_to_maple as Administrator so injected keys reach the game.
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

REM --method postmessage : send Ctrl to the Maple window in the BACKGROUND.
REM                        You can click other windows and it keeps sending.
REM                        BUT DirectInput games often ignore PostMessage -- if
REM                        the character does not react, switch to sendinput below.
REM --method sendinput   : steal focus back to Maple each time, then send.
REM                        Reliable while Maple is the active window, but stops
REM                        working the moment you click away.
REM --interval 0.8       : send Ctrl every 0.8 seconds
"C:\Users\ibuzz\anaconda3\envs\linebot_RAG\python.exe" tools\send_ctrl_to_maple.py --method postmessage --interval 0.8

echo.
echo Finished. Press any key to close.
pause >nul
