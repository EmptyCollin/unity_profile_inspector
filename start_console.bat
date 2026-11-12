@echo off
title UPI Console
setlocal
cd /d "%~dp0"

rem UPI product console launcher.
rem ASCII-only on purpose: a UTF-8 batch file with non-ASCII text can be
rem mis-parsed by cmd.exe before chcp takes effect.
rem The console runs in THIS window (foreground). Closing the window or
rem pressing Ctrl+C terminates the console service. The resident daemon
rem (`python -m upi start`) is a separate process and is NOT affected.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH.
  pause
  exit /b 1
)

if not exist "upi\agents\webui.py" (
  echo [ERROR] upi\agents\webui.py not found. Run this file from the repo root.
  pause
  exit /b 1
)

netstat -ano | findstr /R /C:"LISTENING" | findstr ":8765" >nul
if not errorlevel 1 (
  echo [Console] Port 8765 is already in use.
  echo           Close the window that is running it first, then rerun.
  echo           ^(A token URL is printed only in that window; a bare URL gets 403.^)
  pause
  exit /b 2
)

echo [Console] Starting UPI product console on http://127.0.0.1:8765
echo [Console] Keep this window open while you use the console.
echo [Console] Closing this window or Ctrl+C stops the console service.
echo.
python -m upi ui --port 8765
echo.
echo [Console] exited.
pause
