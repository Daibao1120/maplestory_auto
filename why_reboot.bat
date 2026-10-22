@echo off
cd /d "%~dp0"
chcp 65001 >nul
if not exist data mkdir data
set OUT=data\reboot_report.txt
echo === shutdown / crash events === > %OUT%
wevtutil qe System /rd:true /c:60 /f:text /q:"*[System[(EventID=41 or EventID=1074 or EventID=6008 or EventID=1001 or EventID=6005 or EventID=6006 or EventID=6009 or EventID=19 or EventID=20 or EventID=43 or EventID=109 or EventID=1076 or EventID=161 or EventID=162)]]" >> %OUT% 2>&1
echo === errors and warnings 17:15-17:45 === >> %OUT%
wevtutil qe System /rd:true /c:200 /f:text /q:"*[System[(Level=1 or Level=2 or Level=3) and TimeCreated[@SystemTime>='2026-10-21T09:15:00.000Z' and @SystemTime<='2026-10-21T09:45:00.000Z']]]" >> %OUT% 2>&1
echo === application errors 17:15-17:45 === >> %OUT%
wevtutil qe Application /rd:true /c:100 /f:text /q:"*[System[(Level=1 or Level=2) and TimeCreated[@SystemTime>='2026-10-21T09:15:00.000Z' and @SystemTime<='2026-10-21T09:45:00.000Z']]]" >> %OUT% 2>&1
echo === WHEA hardware errors === >> %OUT%
wevtutil qe Microsoft-Windows-Kernel-WHEA/Operational /rd:true /c:30 /f:text >> %OUT% 2>&1
echo === minidump files === >> %OUT%
dir /o-d C:\Windows\Minidump >> %OUT% 2>&1
echo done. Please tell Claude.
pause
