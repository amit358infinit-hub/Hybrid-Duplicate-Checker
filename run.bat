@echo off
title All Files Duplicate Checker
cd /d "%~dp0sources\desktop_app"

:: Check if Python is installed and in PATH
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo =========================================================
    echo  [ERROR] Python is not installed or not in PATH!
    echo  Please install Python 3.10 or higher from https://www.python.org
    echo  Remember to check "Add Python to PATH" during installation.
    echo =========================================================
    echo.
    pause
    exit /b 1
)

:: Run application
python main.py %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo =========================================================
    echo  [NOTICE] If packages are missing on this system,
    echo  run 'install_requirements.bat' inside sources\desktop_app.
    echo =========================================================
    echo.
    pause
)
