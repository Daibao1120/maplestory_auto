@echo off
REM Monster template collector (READ-ONLY: captures the screen, sends no keys,
REM no admin needed). Stand near the monsters you want templates of, STOP
REM attacking so they stay alive and wander, keep your character still, then
REM double-click this file. Review assets\templates\_candidates\candidates_sheet.png
REM afterwards and move the good crops into assets\templates\monsters\.

pushd "%~dp0.." || (
    echo [ERROR] Cannot enter the repo folder. Run from a local or mapped drive.
    pause
    exit /b 1
)
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

set "PYCMD="
if exist ".venv\Scripts\python.exe" set "PYCMD=.venv\Scripts\python.exe"
if not defined PYCMD for %%V in (3.13 3.12 3.11 3.10 3.9 3.8) do call :probe %%V
if not defined PYCMD set "PYCMD=python"
echo Using Python: %PYCMD%

%PYCMD% tools\collect_monster_templates.py --seconds 90

echo.
echo Done. Open assets\templates\_candidates\candidates_sheet.png to review.
pause >nul
exit /b

:probe
if defined PYCMD exit /b
py -%1 -c "" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b
