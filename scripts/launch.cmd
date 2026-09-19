@echo off
rem Process-scoped policy only; never change the user's PowerShell policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
exit /b %errorlevel%
