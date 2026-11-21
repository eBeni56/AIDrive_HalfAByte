@echo off
rem Sets up .venv (first run only), opens the simulator in the browser, starts the test server.
setlocal
cd /d "%~dp0"
rem Windows Python puts the venv interpreter in Scripts\, MSYS2/MinGW Python in bin\.
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" if exist ".venv\bin\python.exe" set "PY=.venv\bin\python.exe"
if not exist "%PY%" (
  echo Creating .venv ...
  python -m venv .venv || goto :fail
  if not exist "%PY%" set "PY=.venv\bin\python.exe"
)
"%PY%" -m pip install -q -r requirements.txt || goto :fail
start "" "%~dp0index.html"
"%PY%" test_server.py
exit /b
:fail
echo Setup failed. Is Python 3 installed and on PATH?
pause
exit /b 1
