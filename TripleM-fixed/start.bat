@echo off
title Technion Dashboard V3
chcp 65001 >nul
echo.
echo ==========================================
echo   Technion Dashboard V3 - Starting...
echo ==========================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python not found.
    echo Please install from https://python.org
    echo Check "Add Python to PATH" during install!
    pause
    exit /b 1
)

echo Installing / checking dependencies...
python -m pip install flask requests beautifulsoup4 lxml --quiet

echo.
echo Starting server...
echo.
echo  >> Open your browser: http://localhost:5000
echo  >> Press Ctrl+C to stop
echo.

start /b cmd /c "timeout /t 2 >nul && start http://localhost:5000"

python backend\app.py

echo.
echo Server stopped.
pause
