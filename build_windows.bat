@echo off
setlocal EnableDelayedExpansion

set APP_NAME=Voxaho
set VERSION=1.0.1

echo.
echo   Build %APP_NAME%-Setup-%VERSION%.exe
echo   ======================================
echo.

:: ── 1. Verifications ────────────────────────────────────────────────────────
if not exist venv (
    echo [ERREUR] Lance d'abord install.bat
    pause & exit /b 1
)
call venv\Scripts\activate.bat

:: Installer PyInstaller si absent
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo   Installation de PyInstaller...
    pip install pyinstaller --quiet
)

:: Installer Inno Setup si absent (pour le .exe installeur)
where iscc >nul 2>&1
set HAS_INNO=!errorlevel!

:: ── 2. Nettoyer les builds precedents ───────────────────────────────────────
echo   Nettoyage...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

:: ── 3. Compiler avec PyInstaller ────────────────────────────────────────────
echo.
echo   Compilation de %APP_NAME%.exe...

pyinstaller ^
    --name "%APP_NAME%" ^
    --onefile ^
    --windowed ^
    --icon "assets\AppIcon.ico" ^
    --add-data "assets;assets" ^
    --hidden-import "faster_whisper" ^
    --hidden-import "sounddevice" ^
    --hidden-import "numpy" ^
    --hidden-import "pynput" ^
    --hidden-import "pyperclip" ^
    --hidden-import "pyautogui" ^
    --hidden-import "PyQt6" ^
    --collect-all "faster_whisper" ^
    --collect-all "ctranslate2" ^
    main.py

if errorlevel 1 (
    echo [ERREUR] La compilation a echoue.
    pause & exit /b 1
)

if not exist "dist\%APP_NAME%.exe" (
    echo [ERREUR] L'executable n'a pas ete cree.
    pause & exit /b 1
)

echo   dist\%APP_NAME%.exe cree.

:: ── 4. Creer l'installeur Inno Setup (optionnel) ────────────────────────────
if !HAS_INNO! EQU 0 (
    echo.
    echo   Creation de l'installeur avec Inno Setup...
    iscc build_installer.iss
    if not errorlevel 1 (
        echo   %APP_NAME%-Setup-%VERSION%.exe cree.
    )
) else (
    echo.
    echo   [INFO] Inno Setup non installe — seul dist\%APP_NAME%.exe est disponible.
    echo   Pour creer un installeur .exe :
    echo     1. Telecharge Inno Setup : https://jrsoftware.org/isinfo.php
    echo     2. Relance build_windows.bat
)

echo.
echo   ============================================
echo     Build termine : dist\%APP_NAME%.exe
echo   ============================================
echo.
pause
