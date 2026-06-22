@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup-cache.ps1" >nul 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-runtime.ps1"
if errorlevel 1 (
  echo.
  echo MEIAO runtime failed to start. Check startup-runtime.log and runtime-err.log in this folder.
  echo.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8787"
exit /b 0
