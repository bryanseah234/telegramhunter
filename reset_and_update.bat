@echo off
echo ========================================================
echo SYSTEM RESET AND UPDATE
echo WARNING: This will:
echo 1. Delete DATABASE and REDIS data (Sessions preserved)
echo 2. Discard ALL local code changes (Git Reset)
echo ========================================================
echo.
echo 1. Stopping containers...
docker-compose down

echo.
echo 2. Force pulling latest code...
git fetch origin
git reset --hard origin/main

echo.
echo 3. Rebuilding and starting the system...
docker-compose up -d --build

echo.
echo ========================================================
echo Done! System has been updated and reset.
echo ========================================================
pause
