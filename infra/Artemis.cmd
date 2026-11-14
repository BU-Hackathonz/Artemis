@echo off
rem Double-click launcher for the Artemis demo. See infra\launch.ps1 and infra\README.md.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
