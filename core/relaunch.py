"""
Self-relaunch — quitter le process actuel et redémarrer Voxaho.

Utile après que l'utilisateur ait accordé une permission système macOS,
qui ne s'applique qu'aux NOUVEAUX processus (pas aux processus déjà lancés).

  macOS   .app packagée : `open /Applications/Voxaho.app` après quit
  macOS   dev mode      : relance `python main.py` via subprocess
  Windows .exe packagé  : relance sys.executable
  Windows dev mode      : relance python main.py
"""

import logging
import os
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"


def relaunch() -> None:
    """Démarre une nouvelle instance de Voxaho puis quitte la actuelle.

    Ne retourne jamais — la fonction quitte le process.
    """
    try:
        if IS_MAC and _is_app_bundle():
            # App packagée : utilise `open` pour lancer une nouvelle instance.
            # Le -n force une nouvelle instance même si l'app est déjà running.
            app_path = _app_bundle_path()
            logger.info(f"Relaunch via open -n {app_path}")
            subprocess.Popen(
                ["open", "-n", app_path],
                start_new_session=True,
            )
        elif getattr(sys, "frozen", False):
            # PyInstaller bundle (Windows .exe ou autre)
            logger.info(f"Relaunch via {sys.executable}")
            subprocess.Popen(
                [sys.executable],
                start_new_session=True,
            )
        else:
            # Dev mode : python main.py
            main_py = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "main.py")
            )
            logger.info(f"Relaunch via {sys.executable} {main_py}")
            subprocess.Popen(
                [sys.executable, main_py],
                start_new_session=True,
            )

        # Petit délai pour laisser le nouveau process démarrer
        time.sleep(0.3)
    except Exception as e:
        logger.error(f"relaunch failed: {e}", exc_info=True)

    # Quitter le process actuel
    sys.exit(0)


def _is_app_bundle() -> bool:
    """True si on tourne dans un Voxaho.app empaqueté (py2app)."""
    return IS_MAC and ".app/Contents/MacOS/" in sys.executable


def _app_bundle_path() -> str:
    """Retourne le chemin du Voxaho.app bundle."""
    exe = sys.executable
    # exe = /Applications/Voxaho.app/Contents/MacOS/Voxaho
    # on veut /Applications/Voxaho.app
    idx = exe.rfind(".app/")
    if idx == -1:
        return "/Applications/Voxaho.app"  # fallback
    return exe[: idx + 4]


def open_accessibility_settings() -> None:
    """Ouvre les Réglages Système → Confidentialité → Accessibilité (macOS)."""
    if not IS_MAC:
        return
    try:
        subprocess.run([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        ], check=False)
    except Exception as e:
        logger.warning(f"open_accessibility_settings: {e}")
