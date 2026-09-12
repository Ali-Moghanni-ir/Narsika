@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_windows.ps1" %*
set "narsika_exit=%errorlevel%"
if not "%narsika_exit%"=="0" echo Narsika setup did not complete. Read the error above.
if not defined NARSIKA_NO_PAUSE pause
exit /b %narsika_exit%
