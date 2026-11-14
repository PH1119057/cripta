@echo off
setlocal
cd /d "%~dp0"
title Bybit Strategy Workbench

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python environment not found: %CD%\.venv
    echo Run scripts\setup_windows.ps1 once, then start this file again.
    pause
    exit /b 1
)

set "BYBIT_WORKBENCH_PROFILE=live"
set "BYBIT_WORKBENCH_REST_URL="
set "BYBIT_WORKBENCH_PUBLIC_WS_URL="
set "BYBIT_WORKBENCH_PRIVATE_WS_URL="
set "BYBIT_WORKBENCH_ALLOW_LIVE_TRADING="

".venv\Scripts\python.exe" -m bybit_workbench
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Workbench exited with code %RC%.
    pause
)
exit /b %RC%
