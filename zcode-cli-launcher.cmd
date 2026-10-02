@echo off
setlocal
cd /d "%~dp0"
title ZCode GLM CLI Launcher

if "%~1"=="" goto interactive

py zcode_cli_launcher.py %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Launcher exited with code %EXITCODE%.
  echo Press any key to close this window.
  pause >nul
)
exit /b %EXITCODE%

:interactive
py zcode_cli_launcher.py
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" echo Launcher exited with code %EXITCODE%.
echo Press any key to close this window.
pause >nul
exit /b %EXITCODE%
