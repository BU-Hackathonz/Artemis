@echo off
rem Double-click launcher for the Artemis demo. See infra\launch.ps1 and infra\README.md.
rem
rem The window never closes with nothing readable in it: launch.ps1 explains and waits on its own
rem failures, and this file does the same when PowerShell could not run launch.ps1 at all
rem (PowerShell missing or blocked, or the script blocked or damaged). -NoPause never waits.
rem Exit code: launch.ps1's own (0 = the app was closed normally), or PowerShell's when it failed.
setlocal

set "ARTEMIS_NOPAUSE="
for %%A in (%*) do if /i "%%~A"=="-NoPause" set "ARTEMIS_NOPAUSE=1"

rem An empty marker file. launch.ps1 deletes it as its first step, so if it is still there
rem afterwards, the launcher itself never ran. It holds nothing; only its existence matters.
set "ARTEMIS_LAUNCH_MARKER=%TEMP%\artemis-launch-%RANDOM%%RANDOM%.tmp"
type nul > "%ARTEMIS_LAUNCH_MARKER%" 2>nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
set "ARTEMIS_CODE=%ERRORLEVEL%"

if not exist "%ARTEMIS_LAUNCH_MARKER%" goto done
del "%ARTEMIS_LAUNCH_MARKER%" >nul 2>&1
if "%ARTEMIS_CODE%"=="0" set "ARTEMIS_CODE=1"
echo.
echo Artemis could not start: Windows PowerShell could not run the launcher (exit code %ARTEMIS_CODE%).
echo The lines above say why. See infra\README.md, "If the launcher will not start".
if defined ARTEMIS_NOPAUSE goto done
echo.
echo Press any key to close this window.
pause >nul

:done
exit /b %ARTEMIS_CODE%
