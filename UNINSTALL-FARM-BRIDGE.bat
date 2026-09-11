@echo off
REM  Removes the Farm Bridge startup task. Your data and settings are kept
REM  unless you say otherwise when asked.

net session >nul 2>&1
if %errorLevel% == 0 goto :run
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\Uninstall-FarmBridge.ps1"
echo.
pause
