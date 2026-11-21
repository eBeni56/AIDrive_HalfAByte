@echo off
rem Prepares the voice client (copilot\voice_client.py: microphone speech-to-text + text-to-speech) on this PC,
rem first run only, and sets VC_RUN, the command that starts it. Called by start.bat. It runs on the host because
rem Docker Desktop can't reach the microphone or the speakers. Uses uv if it is installed, otherwise Python 3.12
rem from the py launcher; the packages go into copilot\.venv. On a failure VC_RUN stays empty and start.bat goes
rem on without the voice (the dashboard still shows every alert).
set "VC_RUN="
pushd "%~dp0..\copilot"
if exist .venv\Scripts\python.exe goto :ready
echo Setting up the voice client in copilot\.venv - first run only, takes a few minutes ...
where uv >nul 2>&1
if errorlevel 1 goto :py
uv venv -q --python 3.12 .venv
if errorlevel 1 goto :fail
uv pip install -q --python .venv\Scripts\python.exe -r requirements.txt -r ..\speech-recognition\requirements.txt -r ..\text-to-speech\requirements.txt
if errorlevel 1 goto :fail
goto :ready

:py
py -3.12 -m venv .venv
if errorlevel 1 goto :fail
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt -r ..\speech-recognition\requirements.txt -r ..\text-to-speech\requirements.txt
if errorlevel 1 goto :fail

:ready
set "VC_RUN=.venv\Scripts\python.exe -u voice_client.py"
popd
exit /b 0

:fail
echo.
echo The voice client could not be set up (it needs uv or Python 3.12, and internet the first time).
echo Going on without the voice; the next start.bat tries again.
rem remove the half-made venv (it was created just now), so the next start sets it up from the beginning
if exist .venv rmdir /s /q .venv
popd
exit /b 1
