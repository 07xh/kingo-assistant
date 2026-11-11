@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] No Python environment; run run-windows.bat or build-windows.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" windows_gui.py
if errorlevel 1 (
  echo [ERROR] Application startup failed; copy the error above, but remove credentials or school data before sharing.
)
pause
