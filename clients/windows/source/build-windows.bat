@echo off
setlocal
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
%PY% -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10 or newer is required'"
if errorlevel 1 (
  echo [ERROR] Install Python 3.10+ with the Python launcher or Python on PATH.
  pause
  exit /b 1
)
if not exist "dist" mkdir "dist"
if exist "dist\KingoAssistant-Windows.exe" del /q "dist\KingoAssistant-Windows.exe"
if exist "dist\SHA256.txt" del /q "dist\SHA256.txt"
if exist "dist\build-verification.json" del /q "dist\build-verification.json"
%PY% -m venv .venv
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt "pyinstaller>=6,<7"
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m unittest discover -s tests -q
if errorlevel 1 goto :error
".venv\Scripts\python.exe" windows_gui.py --self-test
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name KingoAssistant-Windows --add-data "config.example.yaml;." --add-data "demo;demo" windows_gui.py
if errorlevel 1 goto :error
if not exist "dist\KingoAssistant-Windows.exe" goto :error
".venv\Scripts\python.exe" tools\verify_windows_build.py "dist\KingoAssistant-Windows.exe"
if errorlevel 1 goto :error
echo.
echo [OK] Native Windows EXE passed offline self-test and real Tk GUI smoke: dist\KingoAssistant-Windows.exe
echo [OK] Verification report: dist\build-verification.json
echo [OK] Hash: dist\SHA256.txt
echo [NOTE] No school login, course request, or enrollment has been verified.
pause
exit /b 0
:error
if exist "dist\KingoAssistant-Windows.exe" del /q "dist\KingoAssistant-Windows.exe"
if exist "dist\SHA256.txt" del /q "dist\SHA256.txt"
if exist "dist\build-verification.json" del /q "dist\build-verification.json"
echo [ERROR] Build, offline tests, or EXE self-test failed. No old EXE should be used.
pause
exit /b 1
