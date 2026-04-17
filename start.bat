@echo off
:: ================================================================
:: Telegram Hunter — Launcher
:: Run without arguments for the interactive menu.
:: Or pass a command: start start|stop|restart|build|etc.
:: ================================================================
setlocal enabledelayedexpansion
title Telegram Hunter

:: Jump to dispatch if an argument was given
if not "%~1"=="" goto dispatch

:: ================================================================
::  MENU
:: ================================================================
:menu
cls
echo.
echo   ==========================================
echo    TELEGRAM HUNTER — Control Panel
echo   ==========================================
echo    Automated OSINT System
echo   ==========================================
echo.
echo    OPERATIONS
echo    ----------------------------------------
echo    [1] Start
echo    [2] Start  ^(rebuild images^)
echo    [3] Stop
echo    [4] Restart
echo.
echo    MONITORING
echo    ----------------------------------------
echo    [5] Status
echo    [6] View Logs
echo    [7] Health Check
echo.
echo    MAINTENANCE
echo    ----------------------------------------
echo    [8] Update  ^(pull latest + rebuild^)
echo    [9] Reset   ^(wipe Redis + fresh start^)
echo.
echo    [0] Exit
echo   ==========================================
echo.
set /p "choice=  Choose an option [0-9]: "
echo.

if "%choice%"=="1" goto cmd_start
if "%choice%"=="2" goto cmd_rebuild
if "%choice%"=="3" goto cmd_stop
if "%choice%"=="4" goto cmd_restart
if "%choice%"=="5" goto cmd_status
if "%choice%"=="6" goto menu_logs
if "%choice%"=="7" goto cmd_health
if "%choice%"=="8" goto cmd_update
if "%choice%"=="9" goto cmd_reset
if "%choice%"=="0" goto exit_clean

echo   Invalid option. Please choose 0-9.
timeout /t 2 /nobreak >nul
goto menu

:: ── log sub-menu ─────────────────────────────────────────────────
:menu_logs
cls
echo.
echo   ==========================================
echo    VIEW LOGS
echo   ==========================================
echo    [a] All services
echo    [b] API only
echo    [c] Workers only
echo    [d] Bot only
echo    [0] Back
echo   ==========================================
echo.
set /p "log_choice=  Choose [a/b/c/d/0]: "
echo.

if /i "%log_choice%"=="a" ( docker compose logs -f & goto after_cmd )
if /i "%log_choice%"=="b" ( docker compose logs -f api & goto after_cmd )
if /i "%log_choice%"=="c" ( docker compose logs -f worker-core worker-scanners worker-scrape & goto after_cmd )
if /i "%log_choice%"=="d" ( docker compose logs -f bot & goto after_cmd )
if    "%log_choice%"=="0" goto menu
goto menu_logs

:: ================================================================
::  PORT DETECTION
:: ================================================================
:resolve_ports
echo   Checking ports...
set "API_PORT_PREF=8011"
set "REDIS_PORT_PREF=6379"
if defined API_PORT   set "API_PORT_PREF=%API_PORT%"
if defined REDIS_PORT set "REDIS_PORT_PREF=%REDIS_PORT%"

call :find_free_port %API_PORT_PREF%   API_PORT
call :find_free_port %REDIS_PORT_PREF% REDIS_PORT

echo   OK  API   --^> :%API_PORT%
echo   OK  Redis --^> :%REDIS_PORT%
echo.
goto :eof

:find_free_port
set "_PORT=%~1"
set "_VAR=%~2"
:_port_loop
netstat -ano 2>nul | findstr /R "[:.]%_PORT%  *LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo   WARNING Port %_PORT% in use, trying next...
    set /a "_PORT+=1"
    goto _port_loop
)
set "%_VAR%=%_PORT%"
goto :eof

:: ================================================================
::  COMMANDS
:: ================================================================
:cmd_start
call :resolve_ports
echo   Starting Telegram Hunter...
docker compose up -d
echo.
docker compose ps
goto after_cmd

:cmd_rebuild
call :resolve_ports
echo   Rebuilding images and starting...
docker compose up -d --build
echo.
docker compose ps
goto after_cmd

:cmd_stop
echo   Stopping Telegram Hunter...
docker compose down
echo   All containers stopped.
goto after_cmd

:cmd_restart
echo   Restarting Telegram Hunter...
docker compose down
echo.
call :resolve_ports
docker compose up -d
echo.
docker compose ps
goto after_cmd

:cmd_status
echo   Container Status:
echo.
docker compose ps
goto after_cmd

:cmd_health
set "_API_PORT=8011"
if defined API_PORT set "_API_PORT=%API_PORT%"
echo   Pinging API on :%_API_PORT%...
curl -sf "http://localhost:%_API_PORT%/health/" >nul 2>&1
if errorlevel 1 (
    echo   FAIL  API not responding on :%_API_PORT%
) else (
    echo   OK    API is healthy on :%_API_PORT%
    curl -s "http://localhost:%_API_PORT%/health/detailed"
)
goto after_cmd

:cmd_update
echo   Pulling latest code from GitHub...
git fetch origin
git pull origin main
echo.
echo   Rebuilding images...
docker compose build
echo.
echo   Restarting...
docker compose down
call :resolve_ports
docker compose up -d
echo.
docker compose ps
goto after_cmd

:cmd_reset
echo.
echo   ==========================================
echo    WARNING: RESET
echo   ==========================================
echo    This will:
echo      - Stop all containers
echo      - Wipe Redis data
echo      - Hard-reset code to latest version
echo.
echo    This will NOT touch Supabase or sessions.
echo   ==========================================
echo.
set /p "confirm=   Type YES to confirm: "
if /i not "%confirm%"=="YES" (
    echo   Aborted.
    goto after_cmd
)
echo.
echo   1. Stopping containers and wiping volumes...
docker compose down -v
echo.
echo   2. Resetting code to latest...
git fetch origin
git reset --hard origin/main
echo.
echo   3. Rebuilding and starting...
call :resolve_ports
docker compose up -d --build
echo.
docker compose ps
goto after_cmd

:: ── after each command, pause then return to menu ────────────────
:after_cmd
echo.
pause
goto menu

:: ── direct dispatch (when arg passed on CLI) ─────────────────────
:dispatch
set "CMD=%~1"
if /i "%CMD%"=="start"   goto cmd_start
if /i "%CMD%"=="build"   goto cmd_rebuild
if /i "%CMD%"=="--build" goto cmd_rebuild
if /i "%CMD%"=="stop"    goto cmd_stop
if /i "%CMD%"=="down"    goto cmd_stop
if /i "%CMD%"=="restart" goto cmd_restart
if /i "%CMD%"=="status"  goto cmd_status
if /i "%CMD%"=="ps"      goto cmd_status
if /i "%CMD%"=="health"  goto cmd_health
if /i "%CMD%"=="update"  goto cmd_update
if /i "%CMD%"=="reset"   goto cmd_reset

echo Unknown command: %CMD%
echo Usage: start [start^|build^|stop^|restart^|status^|health^|update^|reset]
exit /b 1

:exit_clean
echo   Bye!
endlocal
exit /b 0
