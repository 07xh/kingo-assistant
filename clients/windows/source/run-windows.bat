@echo off
setlocal
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
%PY% -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if errorlevel 1 goto :missing
if not exist ".venv\Scripts\pythonw.exe" goto :setup
".venv\Scripts\python.exe" -c "import httpx, yaml, bs4, lxml, rich, loguru" >nul 2>nul
if errorlevel 1 goto :setup
:launch
start "KingoAssistant Windows" ".venv\Scripts\pythonw.exe" "%~dp0windows_gui.py"
exit /b 0
:setup
%PY% -m venv .venv
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
goto :launch
:missing
echo [ERROR] Install Python 3.10+ to run the source, or use a built EXE.
pause
exit /b 1
:failed
echo [ERROR] Python setup failed. Run debug-windows.bat for troubleshooting.
pause
exit /b 1
