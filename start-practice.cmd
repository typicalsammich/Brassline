@echo off
cd /d "%~dp0"
set PRACTICE=1
set ONLINE=0
if not exist .venv\Scripts\python.exe (
  echo First run: installing the dashboard. Python 3.11 or newer is required.
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
start "" "http://127.0.0.1:8000"
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000 --ws-max-size 18000 --ws-max-queue 8
pause
exit /b
:failed
echo Setup failed. Keep this window open and tell me the error above.
pause
