@echo off
rem ONE COMMAND FOR EVERYTHING:  start.bat   (after a code change:  start.bat --build)
rem   - server (central_point), simulator, dashboard: Docker ("docker compose up", logs here, Ctrl+C stops them)
rem   - driver monitor (webcam): on this PC, in its own minimised window - Docker Desktop on Windows can't
rem     reach a webcam. Its camera image with the markings is shown on the dashboard.
rem   - safety_ai (speaks the warnings, the LLM coach's tips and debrief): on this PC, in its own minimised
rem     window - Docker can't reach the speakers either. NO_SAFETY_AI=1 skips it; SAI_ARGS goes to it
rem     (e.g.  set SAI_ARGS=--mute  or  --no-llm  or  --voice male_1).
rem   - the dashboard and the simulator open in two browser windows as soon as they answer
rem The two windows left over from an earlier start.bat are stopped first (only one program may use the
rem camera), and they are stopped again when Docker stops here.
rem Extra arguments go to "docker compose up". DM_ARGS goes to the driver monitor, e.g.  set DM_ARGS=--camera 0
setlocal
cd /d "%~dp0"
call :stop_helpers
call "%~dp0scripts\setup-driver-monitor.bat"
if errorlevel 1 exit /b 1
set "SAI_RUN="
if not "%NO_SAFETY_AI%"=="1" call "%~dp0scripts\setup-safety-ai.bat"
start "driver monitor" /min /d "%~dp0driver_monitor" cmd /k %DM_RUN% --no-window --scroll %DM_ARGS%
if defined SAI_RUN start "safety ai" /min /d "%~dp0safety_ai" cmd /k %SAI_RUN% %SAI_ARGS%
start "" /b cmd /c "%~dp0scripts\open-pages.bat"
docker compose up %*
call :stop_helpers
exit /b

:stop_helpers
rem The windows started above are titled "driver monitor" / "safety ai"; /T also ends the Python process in them.
taskkill /F /T /FI "WINDOWTITLE eq driver monitor*" >nul 2>&1
taskkill /F /T /FI "WINDOWTITLE eq safety ai*" >nul 2>&1
exit /b 0
