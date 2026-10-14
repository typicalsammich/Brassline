@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m pip install -r requirements.txt -r worker-requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe worker.py
pause
exit /b
:failed
echo Worker setup failed. Keep this window open and report the error without sharing secrets.
pause
