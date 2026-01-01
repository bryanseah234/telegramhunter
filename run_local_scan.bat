@echo off
echo ==================================================
echo   Telegram Hunter - Local Manual Scan Trigger
echo ==================================================
echo.

REM 1. Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b
)

REM 2. Setup Virtual Environment (if not exists)
if not exist "venv" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

REM 3. Activate Venv
call venv\Scripts\activate

REM 4. Install Dependencies
echo [INFO] Installing/Updating dependencies...
pip install -r requirements.txt >nul

REM 5. Run Scraper Script
echo.
echo [INFO] Step 1: Running CSV Import (if exists)...
if exist "import_tokens.csv" (
    python tests/manual_scrape.py -i import_tokens.csv
)

echo.
echo [INFO] Step 2: Running Full Scanners (Shodan -> URLScan -> GitHub)...
echo.
python tests/manual_scrape.py

echo.
echo ==================================================
echo   Done.
echo ==================================================
pause
