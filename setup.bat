@echo off
setlocal
cd /d "%~dp0"

echo ============================================================================
echo   BUILDING SMART WEB SCRAPER WINDOWS EXECUTABLE (.EXE)
echo ============================================================================

echo.
echo [1/4] Installing application dependencies and PyInstaller...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install dependencies or PyInstaller.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/4] Cleaning previous build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "SmartWebScraper.spec" del /f /q "SmartWebScraper.spec"

echo.
echo [3/4] Compiling standalone executable with PyInstaller...
python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --clean ^
    --name "SmartWebScraper" ^
    --add-data "app.py;." ^
    --collect-all streamlit ^
    --copy-metadata streamlit ^
    --copy-metadata altair ^
    --copy-metadata pandas ^
    --hidden-import streamlit.web.cli ^
    --hidden-import streamlit.runtime.scriptrunner.magic_funcs ^
    --hidden-import pandas ^
    --hidden-import requests ^
    --hidden-import bs4 ^
    --hidden-import lxml ^
    run_app.py

if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller compilation failed.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [4/4] Organizing final "dist" folder for distribution...
copy /y "app.py" "dist\app.py" >nul

echo.
echo ============================================================================
echo   BUILD SUCCESSFUL!
echo   Output Folder: %~dp0dist
echo   Executable:    %~dp0dist\SmartWebScraper.exe
echo   Script Copy:   %~dp0dist\app.py
echo ============================================================================
pause

