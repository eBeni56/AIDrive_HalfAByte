@echo off
rem Waits until the dashboard and the simulator answer, then opens each in its own browser window.
rem Started in the background by start.bat. Ports: environment variable, else the root .env, else the default.
rem PRINT_ONLY=1 only prints what it would open (for testing).
setlocal enabledelayedexpansion
cd /d "%~dp0.."
for %%k in (DASHBOARD_PORT SIMULATOR_PORT) do (
  if not defined %%k if exist .env for /f "tokens=2 delims== " %%p in ('findstr /b /c:"%%k" .env') do set "%%k=%%p"
)
if not defined DASHBOARD_PORT set "DASHBOARD_PORT=8081"
if not defined SIMULATOR_PORT set "SIMULATOR_PORT=8080"
set "URLS=http://localhost:%DASHBOARD_PORT%/ http://localhost:%SIMULATOR_PORT%/"

rem The first start can take a while (images, nginx): retry for up to 3 minutes.
set /a TRIES=0
for %%u in (%URLS%) do (
  call :wait %%u
  if errorlevel 1 exit /b 1
)

rem Chrome or Edge can be asked for a new window; any other default browser opens the pages as tabs.
set "BROWSER="
for %%b in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe") do (
  if not defined BROWSER if exist %%b set "BROWSER=%%~b"
)
for %%u in (%URLS%) do (
  if "%PRINT_ONLY%"=="1" (
    echo open-pages: would open %%u with "!BROWSER!"
  ) else if defined BROWSER (
    start "" "!BROWSER!" --new-window %%u
  ) else (
    start "" %%u
  )
)
exit /b 0

:wait
curl.exe -fs -o nul --max-time 2 %1 && exit /b 0
set /a TRIES+=1
if !TRIES! geq 180 (
  echo open-pages: %1 did not answer, open it by hand.
  exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto :wait
