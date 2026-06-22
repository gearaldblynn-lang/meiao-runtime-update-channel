@echo off
setlocal
cd /d "%~dp0"
echo MEIAO updater
echo.
echo Please close MEIAO before updating.
echo This updates program files only. Local storage, license, media, drafts, and logs are preserved.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-from-git.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
  echo Update failed with exit code %EXIT_CODE%.
  echo Keep this window open and send the message above for diagnosis.
) else (
  echo Update finished.
)
echo.
pause
exit /b %EXIT_CODE%
