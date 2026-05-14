@echo off
if not exist venv (
    echo [ERREUR] Lance d'abord install.bat
    pause & exit /b 1
)
call venv\Scripts\activate.bat
python main.py
