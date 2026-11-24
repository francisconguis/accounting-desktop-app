@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Running Double-Entry, Multi-Currency FX & FIFO Unit Tests
echo ============================================================
echo.

set "PYTHON_CMD="
where python >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    where py >nul 2>nul && set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python 3.12+ was not found on PATH.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating local virtual environment in .venv ...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -c "import pytest, reportlab, pandas, openpyxl" >nul 2>nul
if errorlevel 1 (
    echo Installing required Python packages into .venv ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Package installation failed.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m pytest -v
echo.
pause
endlocal
