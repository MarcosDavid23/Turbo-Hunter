@echo off
setlocal
title Turbo Hunter 0.6.0 - Repair
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1"
if errorlevel 1 pause
exit /b %ERRORLEVEL%
