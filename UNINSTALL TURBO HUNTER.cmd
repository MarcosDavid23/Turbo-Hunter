@echo off
setlocal
title Turbo Hunter 0.6.0 - Uninstall
if not exist "%LOCALAPPDATA%\TurboHunter\TurboHunter\installer\Uninstall.ps1" (
    echo Turbo Hunter is not installed.
    pause
    exit /b 1
)
cd /d "%TEMP%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\TurboHunter\TurboHunter\installer\Uninstall.ps1"
exit /b %ERRORLEVEL%
