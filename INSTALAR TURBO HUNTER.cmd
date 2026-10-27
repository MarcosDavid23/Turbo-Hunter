@echo off
setlocal
chcp 65001 >nul
title Turbo Hunter 0.4.4 - Instalacao visivel

set "BASE=%~dp0"
set "INSTALLER=%BASE%TurboHunter\installer\Installer.ps1"

if not exist "%INSTALLER%" (
    echo.
    echo ERRO: os arquivos internos do instalador nao foram encontrados.
    echo Extraia novamente o ZIP completo antes de instalar.
    echo.
    pause
    exit /b 1
)

echo ================================================================
echo               TURBO HUNTER 0.4.4
echo ================================================================
echo.
echo A instalacao sera exibida na tela.
echo O console de log permanecera visivel durante todo o processo.
echo Nao feche as janelas ate aparecer "Instalacao concluida".
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%"
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" (
    echo O instalador foi encerrado normalmente.
) else (
    echo O instalador terminou com erro %RESULT%.
    echo Consulte TurboHunter\runtime\instalacao.log.
)
echo.
pause
exit /b %RESULT%
