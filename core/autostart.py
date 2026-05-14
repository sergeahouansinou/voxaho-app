"""
Démarrage automatique de Voxaho à l'ouverture de session.

  macOS   → ~/Library/LaunchAgents/com.sergeahouansinou.voxaho.plist
  Windows → HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\Voxaho

Aucun droit admin requis sur les deux plateformes (scope utilisateur).
La vérité est dans le système (plist / registre), pas dans config.json.
"""

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

APP_NAME         = "Voxaho"
LAUNCH_AGENT_ID  = "com.sergeahouansinou.voxaho"


# ── Résolution de la commande à lancer au login ──────────────────────────────

def _executable_command() -> list[str]:
    """Retourne la commande sous forme de liste d'arguments.

    - App packagée macOS (.app via py2app) : sys.executable pointe vers MacOS/Voxaho
    - App packagée Windows (.exe via PyInstaller) : sys.executable pointe vers Voxaho.exe
    - Dev : on relance python main.py avec le venv local
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]

    # Mode dev : on cherche main.py à la racine du projet
    main_py = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "main.py")
    )
    return [sys.executable, main_py]


# ── macOS : LaunchAgent plist ─────────────────────────────────────────────────

def _plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{LAUNCH_AGENT_ID}.plist")


def _plist_content(argv: list[str]) -> str:
    args_xml = "\n        ".join(
        f"<string>{_xml_escape(a)}</string>" for a in argv
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCH_AGENT_ID}</string>
    <key>ProgramArguments</key>
    <array>
        {args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
"""


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _mac_is_enabled() -> bool:
    return os.path.exists(_plist_path())


def _mac_set_enabled(enabled: bool) -> bool:
    plist = _plist_path()
    try:
        if enabled:
            os.makedirs(os.path.dirname(plist), exist_ok=True)
            content = _plist_content(_executable_command())
            tmp = plist + ".tmp"
            with open(tmp, "w") as f:
                f.write(content)
            os.replace(tmp, plist)
            # Recharge pour effet immédiat (sans reboot)
            subprocess.run(["launchctl", "unload", plist], capture_output=True, timeout=5)
            subprocess.run(["launchctl", "load",   plist], capture_output=True, timeout=5)
        else:
            if os.path.exists(plist):
                subprocess.run(["launchctl", "unload", plist], capture_output=True, timeout=5)
                os.remove(plist)
        return True
    except Exception as e:
        logger.error(f"autostart mac: {e}")
        return False


# ── Windows : registre HKCU\Run ───────────────────────────────────────────────

def _win_is_enabled() -> bool:
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            try:
                value, _ = winreg.QueryValueEx(key, APP_NAME)
                return bool(value)
            except FileNotFoundError:
                return False
    except Exception as e:
        logger.error(f"autostart win is_enabled: {e}")
        return False


def _win_set_enabled(enabled: bool) -> bool:
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            if enabled:
                argv = _executable_command()
                # Reg run : commande complète avec quoting pour les espaces
                cmd = " ".join(
                    f'"{a}"' if " " in a else a for a in argv
                )
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        logger.error(f"autostart win set_enabled: {e}")
        return False


# ── API publique ──────────────────────────────────────────────────────────────

def is_supported() -> bool:
    return IS_MAC or IS_WIN


def is_enabled() -> bool:
    """Retourne True si le démarrage auto est actuellement activé au niveau système."""
    if IS_MAC:
        return _mac_is_enabled()
    if IS_WIN:
        return _win_is_enabled()
    return False


def set_enabled(enabled: bool) -> bool:
    """Active ou désactive le démarrage auto. Retourne True en cas de succès."""
    if IS_MAC:
        return _mac_set_enabled(enabled)
    if IS_WIN:
        return _win_set_enabled(enabled)
    return False
