@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0learning\Stop-Learning.ps1"
if errorlevel 1 pause
