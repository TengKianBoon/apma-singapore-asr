@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\Configure_APMA_Credentials.ps1"
if errorlevel 1 pause
endlocal
