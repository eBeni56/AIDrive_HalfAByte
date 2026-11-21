@echo off
rem ONE COMMAND FOR EVERYTHING:  start.bat   (after a code change:  start.bat --build)
rem   - server, simulator, dashboard: Docker ("docker compose up", logs here, Ctrl+C stops them)
rem   - driver monitor (webcam): on this PC, in its own minimised window - Docker Desktop on Windows can't
rem     reach a webcam. Its camera image with the markings is shown on the dashboard. A driver monitor left
rem     over from an earlier start.bat is stopped first (only one may use the camera), and it is stopped
rem     again when Docker stops here.
rem   - the dashboard and the simulator open in two browser windows as soon as they answer
rem Extra arguments go to "docker compose up". DM_ARGS goes to the driver monitor, e.g.  set DM_ARGS=--camera 0
setlocal
cd /d "%~dp0"
call :stop_driver_monitor
call "%~dp0scripts\setup-driver-monitor.bat"
if errorlevel 1 exit /b 1
start "driver monitor" /min /d "%~dp0driver_monitor" cmd /k %DM_RUN% --no-window --scroll %DM_ARGS%
start "" /b cmd /c "%~dp0scripts\open-pages.bat"
docker compose up %*
call :stop_driver_monitor
exit /b

:stop_driver_monitor
rem The window started above is titled "driver monitor"; /T also ends the Python process in it.
taskkill /F /T /FI "WINDOWTITLE eq driver monitor*" >nul 2>&1
exit /b 0
