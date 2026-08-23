@echo off
REM ============================================================
REM  單平台左右打（你的場景）
REM
REM  行為：站在同一個平台上按住攻擊，每 18~30 秒往左或右走一小步以提高
REM  遇怪率；依偵測到的怪決定面向，偵測不到就用經驗值回饋換邊。
REM
REM  安全（全部有測試）：
REM   - 每一步都先確認該方向仍有餘裕，不夠就換邊，兩邊都不夠就不動
REM   - 平台寬度不足 12 一律不巡邏
REM   - 被怪擊退偏離中心會自動推回
REM   - 你一碰方向鍵立刻讓手；F12 完全結束
REM   - 偵測到測謊/符文/死亡彈窗立即零輸入（不自動作答）
REM   - 補血交給你的寵物（腳本完全不碰藥水鍵）
REM
REM  開始前：先跑 run_selftest.bat 確認「腳下平台」寬度足夠。
REM ============================================================

fltmc >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath \"%~f0\" -Verb RunAs"
    exit /b
)

pushd "%~dp0.." || (echo [ERROR] cannot enter repo folder & pause & exit /b 1)
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

set "PYCMD="
if exist ".venv\Scripts\python.exe" set "PYCMD=.venv\Scripts\python.exe"
if not defined PYCMD for %%V in (3.13 3.12 3.11 3.10 3.9 3.8) do call :probe %%V
if not defined PYCMD set "PYCMD=python"
echo Using Python: %PYCMD%

if not exist "logs" mkdir "logs"
%PYCMD% tools\overnight.py --run --dir logs --hours 6

echo.
echo Finished. Log: logs\daemon_log.txt
pause >nul
exit /b

:probe
if defined PYCMD exit /b
py -%1 -c "" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b
