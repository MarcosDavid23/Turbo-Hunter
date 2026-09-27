@echo off
setlocal
chcp 65001 >nul
if /I "%~n0"=="START TURBO HUNTER" goto start

title Turbo Hunter - Install
set "TURBO_SOURCE=%~dp0"
set "TURBO_DEST=%LOCALAPPDATA%\TurboHunter"
if not exist "%TURBO_SOURCE%TurboHunter\installer\InstallerWorker.ps1" (
    echo Please extract the full ZIP before running INSTALL TURBO HUNTER.cmd.
    pause
    exit /b 1
)

if /I not "%TURBO_SOURCE%"=="%TURBO_DEST%\" (
    if not exist "%TURBO_DEST%" mkdir "%TURBO_DEST%"
    robocopy "%TURBO_SOURCE%TurboHunter" "%TURBO_DEST%\TurboHunter" /E /XD runtime /XF hud_config.json /NFL /NDL /NJH /NJS >nul
    if errorlevel 8 (
        echo Could not copy the Turbo Hunter files.
        pause
        exit /b 1
    )
    if not exist "%TURBO_DEST%\TurboHunter\app\hud_config.json" copy /Y "%TURBO_SOURCE%TurboHunter\app\hud_config.json" "%TURBO_DEST%\TurboHunter\app\hud_config.json" >nul
)

echo Installing into %TURBO_DEST% ...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%TURBO_DEST%\TurboHunter\installer\InstallerWorker.ps1"
if errorlevel 1 (
    echo Installation failed. Check %TURBO_DEST%\TurboHunter\runtime\instalacao.log
    pause
    exit /b 1
)

copy /Y "%~f0" "%TURBO_DEST%\START TURBO HUNTER.cmd" >nul
if errorlevel 1 (
    echo Turbo Hunter installed, but START TURBO HUNTER.cmd could not be created.
    pause
    exit /b 1
)

rem Archive only the older installer created by prior Turbo Hunter versions.
if exist "%TURBO_DEST%\INSTALAR TURBO HUNTER.cmd" move /Y "%TURBO_DEST%\INSTALAR TURBO HUNTER.cmd" "%TURBO_DEST%\TurboHunter\installer\INSTALAR TURBO HUNTER.cmd" >nul
echo Installation complete. START TURBO HUNTER.cmd is in %TURBO_DEST%.
echo The desktop shortcut can also launch Turbo Hunter and the game.
pause
exit /b 0

:start
title Turbo Hunter + theHunter
set "TURBO_LAUNCHER=%LOCALAPPDATA%\TurboHunter\TurboHunter\installer\LaunchTogether.ps1"
if not exist "%TURBO_LAUNCHER%" (
    echo Turbo Hunter is not installed. Run INSTALL TURBO HUNTER.cmd from the extracted ZIP.
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%TURBO_LAUNCHER%"
exit /b %ERRORLEVEL%
