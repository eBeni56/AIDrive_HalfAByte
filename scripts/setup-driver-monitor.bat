@echo off
rem Prepares the driver monitor on this PC (first run only) and sets DM_RUN, the command that starts it.
rem Called by start.bat. Uses uv if it is installed (as in driver_monitor/README.md), otherwise Python 3.12
rem from the py launcher with a plain venv in driver_monitor\.venv (the driver monitor needs Python 3.12).
pushd "%~dp0..\driver_monitor"

where uv >nul 2>&1
if errorlevel 1 goto :venv
set "DM_RUN=uv run python -m driver_monitor"
set "DM_PY=uv run python"
goto :models

:venv
if exist .venv\Scripts\python.exe goto :venv_ready
echo Setting up the driver monitor in driver_monitor\.venv - first run only, takes a few minutes ...
py -3.12 -m venv .venv
if errorlevel 1 goto :nopython
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -e .
if errorlevel 1 goto :fail
:venv_ready
set "DM_RUN=.venv\Scripts\python.exe -m driver_monitor"
set "DM_PY=.venv\Scripts\python.exe"

:models
rem face_landmarker.task (face) and efficientdet_lite0.tflite (phone): downloads only what is missing
set "MISSING="
if not exist models\face_landmarker.task set "MISSING=1"
if not exist models\efficientdet_lite0.tflite set "MISSING=1"
if not defined MISSING goto :ok
%DM_PY% tools\download_model.py
if errorlevel 1 goto :fail

:ok
popd
exit /b 0

:nopython
echo.
echo The driver monitor needs uv or Python 3.12. Install one of them:
echo   uv:          powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
echo   Python 3.12: https://www.python.org/downloads/  (then run start.bat again)
:fail
popd
exit /b 1
