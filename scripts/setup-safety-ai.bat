@echo off
rem Prepares safety_ai (the voice and the LLM coach) on this PC (first run only) and sets SAI_RUN, the command
rem that starts it. Called by start.bat. Creates safety_ai\.venv with Python 3.12: with uv if it is installed,
rem otherwise with the py launcher. The voice model (~65 MB) is downloaded by safety_ai itself on its first start.
pushd "%~dp0..\safety_ai"
if exist .venv\Scripts\python.exe goto :ready
echo Setting up safety_ai in safety_ai\.venv - first run only, takes a minute ...
where uv >nul 2>&1
if errorlevel 1 goto :venv
uv venv --python 3.12 .venv -q
if errorlevel 1 goto :fail
uv pip install --python .venv\Scripts\python.exe -q -r requirements.txt
if errorlevel 1 goto :fail
goto :ready

:venv
py -3.12 -m venv .venv
if errorlevel 1 goto :nopython
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

:ready
set "SAI_RUN=.venv\Scripts\python.exe main.py"
if not exist .env echo safety_ai: no safety_ai\.env with the Azure key - only the warnings are spoken (see safety_ai\README.md).
popd
exit /b 0

:nopython
echo.
echo safety_ai needs uv or Python 3.12. Install one of them:
echo   uv:          powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
echo   Python 3.12: https://www.python.org/downloads/  (then run start.bat again)
:fail
if exist .venv rmdir /s /q .venv
popd
exit /b 1
