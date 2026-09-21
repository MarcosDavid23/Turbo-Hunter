@echo off
setlocal
chcp 65001 >nul
color 0B
set "TURBO_BASE=%~dp0"
set "TURBO_INSTALLER=%TURBO_BASE%TurboHunter\installer\InstallerWorker.ps1"
set "TURBO_LANG=en"
for /f "delims=" %%L in ('powershell.exe -NoProfile -Command "[Globalization.CultureInfo]::CurrentUICulture.TwoLetterISOLanguageName" 2^>nul') do set "TURBO_LANG=%%L"
if /I "%TURBO_LANG%"=="pt" goto :cabecalho_pt

title Turbo Hunter 0.5.4 - Install
echo ================================================================
echo                         TURBO HUNTER 0.5.4
echo ================================================================
echo Visible installation: every step is shown here and saved in
echo TurboHunter\runtime\instalacao.log.
echo.
goto :verificar

:cabecalho_pt
title Turbo Hunter 0.5.4 - Instalar
echo ================================================================
echo                         TURBO HUNTER 0.5.4
echo ================================================================
echo Instalacao visivel: cada etapa aparece aqui e em
echo TurboHunter\runtime\instalacao.log.
echo.

:verificar
if not exist "%TURBO_INSTALLER%" (
    if /I "%TURBO_LANG%"=="pt" (
        echo ERRO: extraia o ZIP completo antes de instalar.
    ) else (
        echo ERROR: extract the complete ZIP before installing.
    )
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%TURBO_INSTALLER%"
set "TURBO_RESULTADO=%ERRORLEVEL%"
echo.
if "%TURBO_RESULTADO%"=="0" (
    if /I "%TURBO_LANG%"=="pt" (
        echo Instalacao concluida. O menu Turbo Hunter ja foi solicitado.
        echo Nas proximas vezes, use INICIAR TURBO HUNTER.vbs.
        echo Este instalador inicial sera removido ao fechar.
    ) else (
        echo Installation complete. The Turbo Hunter menu has been opened.
        echo Next time, use INICIAR TURBO HUNTER.vbs.
        echo This initial installer will be removed when it closes.
    )
) else (
    if /I "%TURBO_LANG%"=="pt" (
        echo Instalacao incompleta. Consulte o erro acima ou instalacao.log.
        echo Este arquivo continuara aqui para voce tentar novamente.
    ) else (
        echo Installation incomplete. Check the error above or instalacao.log.
        echo This file will remain here so you can try again.
    )
)
if /I "%TURBO_LANG%"=="pt" (
    echo Pressione qualquer tecla para fechar a instalacao.
) else (
    echo Press any key to close the installer.
)
pause >nul
if "%TURBO_RESULTADO%"=="0" del /Q "%~f0" >nul 2>&1
exit /b %TURBO_RESULTADO%
