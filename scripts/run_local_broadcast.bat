@echo off
echo ==================================================
echo   Telegram Hunter - Local Manual Broadcaster
echo   (Sends Pending Messages to Telegram)
echo ==================================================
echo.

REM 1. Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b
)

REM 2. Activate Venv (if exists)
if exist "..\venv" (
    call ..\venv\Scripts\activate
) else (
    echo [INFO] No venv found, assuming global python...
)

REM 3. Run Broadcast Script
echo.
echo [INFO] Starting Broadcaster...
echo.
python ..\tests\manual_broadcast.py

echo.
echo ==================================================
echo   Done.
echo ==================================================
pause
