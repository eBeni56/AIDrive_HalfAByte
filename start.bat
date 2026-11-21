@echo off
rem ONE COMMAND FOR EVERYTHING:  start.bat   (after a code change:  start.bat --build)
rem   - server, simulator, dashboard, ollama, ollama-pull (downloads the language models on the first start)
rem     and copilot (the AI): Docker ("docker compose up", logs here, Ctrl+C stops them). When an NVIDIA GPU is
rem     found (nvidia-smi), compose.gpu.yml is added so Ollama runs on it; set OLLAMA_GPU=0 / 1 forces it.
rem   - driver monitor (webcam): on this PC, in its own minimised window - Docker Desktop on Windows can't
rem     reach a webcam. Its camera image with the markings is shown on the dashboard. A driver monitor left
rem     over from an earlier start.bat is stopped first (only one may use the camera), and it is stopped
rem     again when Docker stops here.
rem   - voice client (microphone + speakers, which Docker can't reach either): on this PC, in its own minimised
rem     window. It speaks the copilot's alerts and answers "Hey Jarvis ...". The first start sets it up in
rem     copilot\.venv. VOICE_ARGS goes to it (default --mic; e.g. set VOICE_ARGS=--text), NO_VOICE=1 skips it.
rem   - the dashboard and the simulator open in two browser windows as soon as they answer
rem Extra arguments go to "docker compose up". DM_ARGS goes to the driver monitor, e.g.  set DM_ARGS=--camera 0
setlocal
cd /d "%~dp0"
call :stop_helpers
call "%~dp0scripts\setup-driver-monitor.bat"
if errorlevel 1 exit /b 1
set "VC_RUN="
if not "%NO_VOICE%"=="1" call "%~dp0scripts\setup-voice.bat"
if not defined VOICE_ARGS set "VOICE_ARGS=--mic"

if not defined OLLAMA_GPU (
  set "OLLAMA_GPU=0"
  where nvidia-smi >nul 2>&1 && set "OLLAMA_GPU=1"
)
set "COMPOSE_FILES=-f compose.yml"
if "%OLLAMA_GPU%"=="1" set "COMPOSE_FILES=-f compose.yml -f compose.gpu.yml"
if "%OLLAMA_GPU%"=="1" (echo Ollama runs on the NVIDIA GPU.) else (echo Ollama runs on the CPU: answers to questions are slow - the safety alerts are not affected.)

start "driver monitor" /min /d "%~dp0driver_monitor" cmd /k %DM_RUN% --no-window --scroll %DM_ARGS%
if defined VC_RUN start "voice client" /min /d "%~dp0copilot" cmd /k %VC_RUN% %VOICE_ARGS%
start "" /b cmd /c "%~dp0scripts\open-pages.bat"
docker compose %COMPOSE_FILES% up %*
call :stop_helpers
exit /b

:stop_helpers
rem The windows started above are titled "driver monitor" / "voice client"; /T also ends the Python process in them.
taskkill /F /T /FI "WINDOWTITLE eq driver monitor*" >nul 2>&1
taskkill /F /T /FI "WINDOWTITLE eq voice client*" >nul 2>&1
exit /b 0
