@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   FSE Processor - Build Script
echo ============================================
echo.

:: ---- Step 1: Check Python ----
echo [1/6] Verifica presenza di Python...
set "PYTHON_CMD="

echo       Ricerca comando "python"...
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :python_found
)

echo       Ricerca comando "py -3" (Python Launcher)...
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto :python_found
)

:: Python not found - offer automatic installation
echo.
echo [AVVISO] Python non e' installato nel sistema.
echo          Python e' necessario per compilare FSE Processor.
echo.
choice /c SN /m "Vuoi installare Python automaticamente"
if errorlevel 2 goto :python_declined

:: ---- Automatic Python Installation ----
echo.
echo ============================================
echo   Installazione automatica di Python
echo ============================================
echo.

:: Try winget first (available on modern Windows 10/11)
echo [PASSO 1] Verifica disponibilita' di Windows Package Manager (winget)...
winget --version >nul 2>&1
if not errorlevel 1 (
    echo          winget trovato.
    echo [PASSO 2] Installazione Python 3.12 tramite winget...
    echo          Esecuzione: winget install -e --id Python.Python.3.12
    echo.
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    if not errorlevel 1 (
        echo.
        echo [OK] Python installato tramite winget.
        goto :python_post_install
    )
    echo.
    echo [AVVISO] Installazione tramite winget fallita, provo download diretto...
    echo.
) else (
    echo          winget non disponibile, uso download diretto.
    echo.
)

:: Fallback: download installer from python.org
set "PYTHON_URL=https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
set "PYTHON_INSTALLER=%TEMP%\python_installer.exe"

echo [PASSO 2] Download installer di Python...
echo          URL: !PYTHON_URL!
echo          Destinazione: !PYTHON_INSTALLER!
echo.

echo          Tentativo con curl...
curl -L --progress-bar -o "!PYTHON_INSTALLER!" "!PYTHON_URL!" 2>nul
if errorlevel 1 (
    echo          curl non disponibile, tentativo con bitsadmin...
    bitsadmin /transfer "PythonDownload" /download /priority high "!PYTHON_URL!" "!PYTHON_INSTALLER!" 2>&1
    if errorlevel 1 (
        echo.
        echo [ERRORE] Download di Python fallito.
        echo          Verifica la connessione a Internet e riprova,
        echo          oppure installa Python manualmente da https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
echo [OK] Download completato.
echo.

echo [PASSO 3] Avvio installazione Python...
echo          Modalita': silenziosa (nessuna interazione richiesta)
echo          Opzioni: PrependPath=1 Include_launcher=1 Include_pip=1
echo          Questa operazione potrebbe richiedere qualche minuto...
echo.
"!PYTHON_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1
if errorlevel 1 (
    echo [AVVISO] Installazione silenziosa fallita.
    echo          Avvio installazione guidata (seguire le istruzioni a schermo)...
    echo.
    "!PYTHON_INSTALLER!" PrependPath=1 Include_launcher=1 Include_pip=1
    if errorlevel 1 (
        echo.
        echo [ERRORE] Installazione Python fallita.
        echo          Prova ad installare manualmente da https://www.python.org/downloads/
        echo          Assicurati di selezionare "Add Python to PATH".
        del "!PYTHON_INSTALLER!" 2>nul
        pause
        exit /b 1
    )
)

echo [OK] Installazione Python completata.
echo          Pulizia file temporanei...
del "!PYTHON_INSTALLER!" 2>nul

:python_post_install
echo.
echo [PASSO 4] Aggiornamento variabili d'ambiente (PATH)...
echo          Lettura PATH dal registro di sistema...

:: Refresh PATH from registry so the current session sees the new Python
set "NEW_PATH="
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "NEW_PATH=%%b"
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul') do (
    if defined NEW_PATH (
        set "NEW_PATH=!NEW_PATH!;%%b"
    ) else (
        set "NEW_PATH=%%b"
    )
)
if defined NEW_PATH set "PATH=!NEW_PATH!"
echo [OK] PATH aggiornato.

:: Verify Python is now available
echo.
echo [PASSO 5] Verifica che Python sia raggiungibile...

set "PYTHON_CMD="
echo          Ricerca comando "python"...
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :python_found
)
echo          Ricerca comando "py -3"...
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto :python_found
)

:: Check common install locations as last resort
echo          Ricerca in percorsi di installazione comuni...
for %%p in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python313\python.exe"
) do (
    if exist %%~p (
        echo          Trovato: %%~p
        set "PYTHON_CMD=%%~p"
        goto :python_found
    )
)

echo.
echo [ERRORE] Python e' stato installato ma non e' raggiungibile nella sessione corrente.
echo          Chiudi questa finestra, aprine una nuova e riesegui build.bat
pause
exit /b 1

:: ---- User declined installation ----
:python_declined
echo.
echo ============================================
echo   Installazione interrotta
echo ============================================
echo.
echo Per compilare FSE Processor e' necessario Python 3.10 o superiore.
echo.
echo Puoi installarlo manualmente:
echo   1. Scarica Python da https://www.python.org/downloads/
echo   2. Durante l'installazione, seleziona "Add Python to PATH"
echo   3. Riesegui build.bat
echo.
pause
exit /b 1

:: ---- Python found / installed successfully ----
:python_found
for /f "tokens=*" %%v in ('!PYTHON_CMD! --version 2^>^&1') do set "PYTHON_VERSION=%%v"
echo [OK] !PYTHON_VERSION!
echo.

:: Set PIP command based on Python command
set "PIP_CMD=!PYTHON_CMD! -m pip"

:: ---- Step 2: Check/install PyInstaller ----
echo [2/6] Verifica PyInstaller...
echo       Controllo se PyInstaller e' gia' installato...
!PYTHON_CMD! -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo       PyInstaller non trovato, installazione in corso...
    echo       Esecuzione: !PIP_CMD! install pyinstaller
    !PIP_CMD! install pyinstaller
    if errorlevel 1 (
        echo [ERRORE] Installazione PyInstaller fallita
        pause
        exit /b 1
    )
)
echo [OK] PyInstaller disponibile
echo.

:: ---- Step 3: Install dependencies ----
echo [3/6] Installazione dipendenze da requirements.txt...
echo       Esecuzione: !PIP_CMD! install -r requirements.txt
!PIP_CMD! install -r requirements.txt >nul 2>&1
echo [OK] Dipendenze installate
echo.

:: ---- Step 4: Clean & Build ----
echo [4/6] Compilazione con PyInstaller...

echo       Pulizia build precedente...
if exist dist (
    echo       Rimozione cartella dist\...
    rmdir /s /q dist
)
if exist build (
    echo       Rimozione cartella build\...
    rmdir /s /q build
)
echo       Pulizia completata.
echo.

echo       Avvio PyInstaller...
echo       Esecuzione: !PYTHON_CMD! -m PyInstaller fse_processor.spec --noconfirm
echo.
!PYTHON_CMD! -m PyInstaller fse_processor.spec --noconfirm
if errorlevel 1 (
    echo.
    echo [ERRORE] PyInstaller build fallito
    pause
    exit /b 1
)
echo.
echo [OK] PyInstaller build completato
echo.

:: ---- Step 5: Verify output ----
echo [5/6] Verifica risultato build...

echo       Ricerca eseguibile...
if not exist "dist\FSE Processor\FSE Processor.exe" (
    echo [ERRORE] Eseguibile non trovato in dist\FSE Processor\
    pause
    exit /b 1
)
echo [OK] Eseguibile trovato: dist\FSE Processor\FSE Processor.exe

echo       Verifica Playwright driver...
if not exist "dist\FSE Processor\playwright\driver\node.exe" (
    echo [ATTENZIONE] Playwright driver node.exe non trovato nel bundle
    echo              Il browser integrato Chromium potrebbe non funzionare
) else (
    echo [OK] Playwright driver incluso
)
echo.

:: ---- Step 6: Create Installer ----
echo [6/6] Generazione installer con Inno Setup...

set "ISCC="
echo       Ricerca Inno Setup 6...
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
)
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
)

if defined ISCC (
    echo       Trovato: !ISCC!
    if not exist installer_output mkdir installer_output
    echo       Compilazione installer.iss...
    echo.
    "!ISCC!" installer.iss
    if errorlevel 1 (
        echo.
        echo [ERRORE] Inno Setup build fallito
        pause
        exit /b 1
    )
    echo.
    echo [OK] Installer creato in installer_output\
) else (
    echo       Inno Setup non trovato. Installer non generato.
    echo       Installa Inno Setup 6 da: https://jrsoftware.org/isinfo.php
    echo       Poi riesegui build.bat per generare l'installer.
)

echo.
echo ============================================
echo   Build completato con successo!
echo ============================================
echo   Eseguibile: dist\FSE Processor\FSE Processor.exe
if defined ISCC (
    echo   Installer:  installer_output\FSE_Processor_Setup_1.0.0.exe
)
echo ============================================
pause
