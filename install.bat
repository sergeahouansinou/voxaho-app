@echo off
setlocal EnableDelayedExpansion

echo.
echo   Voxaho — Installation Windows
echo   ==================================
echo.

:: ── 1. Verifier Python ──────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python introuvable.
    echo   Telecharge Python 3.10+ sur https://www.python.org/downloads/
    echo   Coche "Add Python to PATH" lors de l'installation.
    pause & exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set MAJOR=%%a
    set MINOR=%%b
)
set /a PYCHECK=MAJOR*100+MINOR
if !PYCHECK! LSS 310 (
    echo [ERREUR] Python 3.10+ requis. Version actuelle : !PYVER!
    pause & exit /b 1
)
echo   Python !PYVER! detecte.

:: ── 2. Creer le venv ────────────────────────────────────────────────────────
if not exist venv (
    echo.
    echo   Creation de l'environnement virtuel...
    python -m venv venv
    if errorlevel 1 (
        echo [ERREUR] Impossible de creer le venv.
        pause & exit /b 1
    )
)

:: ── 3. Installer les dependances ────────────────────────────────────────────
echo.
echo   Installation des dependances...
call venv\Scripts\activate.bat

python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERREUR] L'installation des dependances a echoue.
    pause & exit /b 1
)

:: ── 4. Pre-telecharger le modele Whisper ────────────────────────────────────
echo.
echo   Telechargement du modele Whisper 'small' (~460 Mo)...
echo   (Ce telechargement n'a lieu qu'une seule fois)
python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('   Modele pret.')"
if errorlevel 1 (
    echo [AVERTISSEMENT] Le modele sera telecharge au premier lancement.
)

echo.
echo   ============================================
echo     Installation terminee !
echo     Lance Voxaho avec :  run.bat
echo   ============================================
echo.
pause
