@echo off
rem Starts everything with Docker (like "docker compose up") and opens the dashboard and the simulator
rem in two browser windows as soon as they answer. Ctrl+C stops everything, as usual.
rem Extra arguments go to "docker compose up", e.g.   start.bat --build
setlocal
cd /d "%~dp0"
start "" /b cmd /c "%~dp0scripts\open-pages.bat"
docker compose up %*
