@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   FSE Processor - Build Script
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRORE] Python non trovato. Installalo da python.org
    pause
    exit /b 1
)
echo [OK] Python trovato

:: Check/install PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [INFO] PyInstaller non trovato, installazione in corso...
    pip install pyinstaller
    if errorlevel 1 (
        echo [ERRORE] Installazione PyInstaller fallita
        pause
        exit /b 1
    )
)
echo [OK] PyInstaller disponibile

:: Check dependencies
pip install -r requirements.txt >nul 2>&1
echo [OK] Dipendenze installate

:: Clean previous build
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
echo [OK] Pulizia build precedente

:: Run PyInstaller
echo.
echo [BUILD] Avvio PyInstaller...
pyinstaller fse_processor.spec --noconfirm
if errorlevel 1 (
    echo [ERRORE] PyInstaller build fallito
    pause
    exit /b 1
)
echo [OK] PyInstaller build completato

:: Verify output
if not exist "dist\FSE Processor\FSE Processor.exe" (
    echo [ERRORE] Eseguibile non trovato in dist\FSE Processor\
    pause
    exit /b 1
)
echo [OK] Eseguibile trovato

:: Verify Playwright driver is included
if not exist "dist\FSE Processor\playwright\driver\node.exe" (
    echo [ATTENZIONE] Playwright driver node.exe non trovato nel bundle
    echo              Il browser integrato Chromium potrebbe non funzionare
) else (
    echo [OK] Playwright driver incluso
)

:: Check for Inno Setup
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
)
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
)

if defined ISCC (
    echo.
    echo [BUILD] Avvio Inno Setup Compiler...
    if not exist installer_output mkdir installer_output
    "!ISCC!" installer.iss
    if errorlevel 1 (
        echo [ERRORE] Inno Setup build fallito
        pause
        exit /b 1
    )
    echo [OK] Installer creato in installer_output\
) else (
    echo.
    echo [INFO] Inno Setup non trovato. Installer non generato.
    echo        Installa Inno Setup 6 da: https://jrsoftware.org/isinfo.php
    echo        Poi riesegui build.bat per generare l'installer.
)

echo.
echo ============================================
echo   Build completato!
echo ============================================
echo   Eseguibile: dist\FSE Processor\FSE Processor.exe
if defined ISCC (
    echo   Installer:  installer_output\FSE_Processor_Setup_1.0.0.exe
)
echo ============================================
pause
