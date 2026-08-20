@echo off
REM End-to-end readiness check (READ-ONLY: never sends keys, no admin needed).
REM Start the game and stand where you want to farm, then run this.
pushd "%~dp0.." || (echo [ERROR] cannot enter repo & pause & exit /b 1)
set PYTHONIOENCODING=utf-8
set "PYCMD="
if exist ".venv\Scripts\python.exe" set "PYCMD=.venv\Scripts\python.exe"
if not defined PYCMD for %%V in (3.13 3.12 3.11 3.10 3.9 3.8) do call :probe %%V
if not defined PYCMD set "PYCMD=python"
%PYCMD% tools\selftest.py --seconds 45
echo.
pause >nul
exit /b
:probe
if defined PYCMD exit /b
py -%1 -c "" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -%1"
exit /b
