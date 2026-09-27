@echo off
setlocal
chcp 65001 >nul
color 0B
title Turbo Hunter 0.5.7 - Reparar
set "SETUP=%~dp0InstallerWorker.ps1"
if not exist "%SETUP%" (
    echo ERRO: o instalador interno nao foi encontrado. Extraia novamente o ZIP completo.
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SETUP%"
set "RESULTADO=%ERRORLEVEL%"
echo.
if "%RESULTADO%"=="0" (
    echo Instalacao concluida. Use START TURBO HUNTER.cmd.
) else (
    echo Instalacao incompleta. Detalhes em TurboHunter\runtime\instalacao.log.
)
echo Pressione qualquer tecla para fechar este instalador.
pause >nul
exit /b %RESULTADO%
