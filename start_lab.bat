@echo off
title I8 Exam Lab
setlocal
cd /d "%~dp0"

rem I8 exam-paper bench (port 8787). Generates test_project and scores it.
rem No Unity needed here: the tool under test is started separately via run.bat.
rem ASCII-only on purpose.

if not exist "scripts\i8_lab.py" (
  echo [ERROR] scripts\i8_lab.py not found. Run this file from the repo root.
  pause
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH.
  pause
  exit /b 1
)

netstat -ano | findstr /R /C:"LISTENING" | findstr ":8787" >nul
if not errorlevel 1 (
  echo [Lab] Port 8787 already serving. Opening browser.
  start "" http://127.0.0.1:8787
  exit /b 0
)

echo [Lab] Starting I8 exam bench on http://127.0.0.1:8787
echo [Lab] Keep this window open while you use it.
echo [Lab] Closing this window or Ctrl+C stops the bench.
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8787'"
python scripts\i8_lab.py --port 8787
echo.
echo [Lab] exited.
pause
