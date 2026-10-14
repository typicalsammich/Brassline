@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe worker.py --login
pause
exit /b
:failed
echo Install Python 3.11 or newer before opening the dedicated profile.
pause
