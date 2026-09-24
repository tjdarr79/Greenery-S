@echo off
REM ===========================================================================
REM  Greenery S Farm Bridge - Windows Installer
REM
REM  DOUBLE-CLICK THIS FILE. That is the whole instruction.
REM
REM  It asks for Administrator permission (needed to install Python and to
REM  create the startup task), then does everything else on its own.
REM ===========================================================================

net session >nul 2>&1
if %errorLevel% == 0 goto :run

echo.
echo  Requesting Administrator permission...
echo  Click YES on the Windows prompt that appears.
echo.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\Install-FarmBridge.ps1"

echo.
echo  ==========================================================
echo   Installer finished. Read the messages above.
echo  ==========================================================
echo.
pause
