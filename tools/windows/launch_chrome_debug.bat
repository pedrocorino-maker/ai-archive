@echo off
:: launch_chrome_debug.bat
:: Double-click this file to launch Chrome with CDP debugging enabled.
:: Wrapper around launch_chrome_debug.ps1

echo Launching Chrome with remote debugging...
PowerShell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_chrome_debug.ps1"
pause
