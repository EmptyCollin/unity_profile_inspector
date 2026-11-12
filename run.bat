@echo off
title UPI Run
setlocal
cd /d "%~dp0"

rem UPI entry point: verify prerequisites, then start the product console.
rem ASCII-only and free of cmd-hostile characters inside IF blocks.

echo [1/4] Checking repository layout ...
if not exist "scripts\i8_lab.py" goto :no_repo
if not exist "sim\project\Assets" goto :no_project

echo [2/4] Checking python ...
where python >nul 2>nul
if errorlevel 1 goto :no_python
python -V

echo [3/4] Checking unity CLI ...
where unity >nul 2>nul
if errorlevel 1 goto :no_unity

echo [4/4] Checking Unity editor ...
tasklist /FI "IMAGENAME eq Unity.exe" 2>nul | findstr /I "Unity.exe" >nul
if errorlevel 1 (
  echo   WARN: no Unity Editor process found.
  echo   Open sim\project in Unity 6000.6.x, or run: python scripts\ensure_editor.py
  echo   The console still starts; editor-dependent actions fail until it is open.
) else (
  echo   editor process found.
)

echo.
echo Starting product console via start_console.bat ...
call start_console.bat
goto :eof

:no_repo
echo ERROR: run this file from the UPI repository root.
pause
exit /b 1

:no_project
echo ERROR: sim\project is missing. Restore the repository before running.
pause
exit /b 1

:no_python
echo ERROR: python not found in PATH.
echo   Install Python 3.12 or newer and enable "Add python.exe to PATH".
echo   Reopen this window and run again.
pause
exit /b 1

:no_unity
echo ERROR: unity CLI not found in PATH.
echo   It ships with Unity 6 (6000.6.x). If missing, reinstall Unity 6 via Unity Hub
echo   or add the Unity tools folder to PATH.
pause
exit /b 1
