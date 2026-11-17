@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm ftp_sync_tool.spec
if errorlevel 1 goto fail
echo.
echo Built: dist\ftp_sync_tool.exe
pause
exit /b 0
:fail
echo Build failed. See errors above.
pause
exit /b 1
