@echo off
title I8 Lab
setlocal
cd /d "%~dp0"

rem I8 Lab one-click start (development/acceptance bench, port 8787).
rem ASCII-only on purpose: a UTF-8 batch file with non-ASCII text can be
rem mis-parsed by cmd.exe before chcp takes effect, which makes the window
rem close instantly. Keep this file plain ASCII.
rem The Lab runs in THIS window (foreground). Closing the window or Ctrl+C
rem stops it; the Unity editor is left running.

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
  echo [Lab] Port 8787 is already serving. Opening browser.
  start "" http://127.0.0.1:8787
  exit /b 0
)

echo [1/2] Checking Unity editor ...
python scripts\ensure_editor.py --wait 240
if errorlevel 2 (
  echo.
  echo [STOP] A Unity window exists but Pipeline is unreachable.
  echo        Do NOT open a second editor. Finish loading it, then rerun.
  pause
  exit /b 2
)
if errorlevel 1 (
  echo.
  echo [STOP] Timed out waiting for Unity. It may still be starting; rerun later.
  pause
  exit /b 1
)

echo [2/2] Starting I8 Lab on http://127.0.0.1:8787
echo [Lab] Keep this window open while you use the Lab.
echo [Lab] Closing this window or Ctrl+C stops the Lab (Unity stays open).
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8787'"
python scripts\i8_lab.py --port 8787
echo.
echo [Lab] exited.
pause
