@echo off
rem Double-click to start the Atelic x Dell demo. Other commands: atelicxdell stop ^| reset ^| status ^| logs
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0atelicxdell.ps1" %*
if errorlevel 1 pause
