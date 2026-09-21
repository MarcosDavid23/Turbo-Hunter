@echo off
setlocal
chcp 65001 >nul
color 0B
set "SETUP=%~dp0InstallerWorker.ps1"
set "TURBO_LANG=en"
for /f "delims=" %%L in ('powershell.exe -NoProfile -Command "[Globalization.CultureInfo]::CurrentUICulture.TwoLetterISOLanguageName" 2^>nul') do set "TURBO_LANG=%%L"
if /I "%TURBO_LANG%"=="pt" (
    title Turbo Hunter 0.5.4 - Reinstalar
) else (
    title Turbo Hunter 0.5.4 - Reinstall
)
if not exist "%SETUP%" (
    if /I "%TURBO_LANG%"=="pt" (
        echo ERRO: o instalador interno nao foi encontrado. Extraia novamente o ZIP completo.
    ) else (
        echo ERROR: the internal installer was not found. Extract the complete ZIP again.
    )
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SETUP%"
set "RESULTADO=%ERRORLEVEL%"
echo.
if "%RESULTADO%"=="0" (
    if /I "%TURBO_LANG%"=="pt" (
        echo Reinstalacao concluida. Use INICIAR TURBO HUNTER.vbs.
    ) else (
        echo Reinstallation complete. Use INICIAR TURBO HUNTER.vbs.
    )
) else (
    if /I "%TURBO_LANG%"=="pt" (
        echo Reinstalacao incompleta. Detalhes em TurboHunter\runtime\instalacao.log.
        echo Corrija o problema indicado e tente novamente.
    ) else (
        echo Reinstallation incomplete. Details are in TurboHunter\runtime\instalacao.log.
        echo Fix the reported problem and try again.
    )
)
if /I "%TURBO_LANG%"=="pt" (
    echo Pressione qualquer tecla para fechar este instalador.
) else (
    echo Press any key to close this installer.
)
pause >nul
exit /b %RESULTADO%
