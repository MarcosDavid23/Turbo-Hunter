@echo off
setlocal
chcp 65001 >nul
title Turbo Hunter 0.6.0
if not exist "%~dp0TurboHunter\installer\Install.ps1" (
    echo Extraia o ZIP inteiro antes de instalar. / Extract the entire ZIP before installing.
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TurboHunter\installer\Install.ps1"
if errorlevel 1 (
    echo.
    echo Detalhes / Details: %%LOCALAPPDATA%%\TurboHunter\install_log.txt
    pause
    exit /b 1
)
pause
exit /b 0
