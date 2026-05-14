"""
Injection du texte transcrit dans l'application active.

  macOS   → pbcopy + Cmd+V  |  fallback : osascript keystroke
  Windows → pyperclip + Ctrl+V  |  fallback : pyautogui.write()
"""

import sys
import time
import logging
import subprocess

logger = logging.getLogger(__name__)

IS_MAC   = sys.platform == "darwin"
DELAY    = 0.15   # délai entre copie et collage


def inject_text(text: str) -> None:
    if not text:
        return
    if IS_MAC:
        _inject_mac(text)
    else:
        _inject_windows(text)


# ── macOS ──────────────────────────────────────────────────────────────────

def _inject_mac(text: str) -> None:
    try:
        _clipboard_mac(text)
        time.sleep(DELAY)
        _paste_mac()
    except Exception as e:
        logger.warning(f"inject_mac principal échoué: {e}")
        _fallback_applescript(text)


def _clipboard_mac(text: str) -> None:
    proc = subprocess.run(
        ["pbcopy"],
        input=text.encode("utf-8"),
        capture_output=True,
        timeout=3,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pbcopy: {proc.stderr.decode()}")


def _paste_mac() -> None:
    import pyautogui
    pyautogui.PAUSE = 0
    pyautogui.hotkey("command", "v")


def _fallback_applescript(text: str) -> None:
    try:
        import json
        script = f'tell application "System Events" to keystroke {json.dumps(text)}'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception as e:
        logger.error(f"AppleScript fallback: {e}")


# ── Windows ────────────────────────────────────────────────────────────────

def _inject_windows(text: str) -> None:
    try:
        _clipboard_windows(text)
        time.sleep(DELAY)
        _paste_windows()
    except Exception as e:
        logger.warning(f"inject_windows principal échoué: {e}")
        _fallback_windows(text)


def _clipboard_windows(text: str) -> None:
    try:
        import pyperclip
        pyperclip.copy(text)
    except ImportError:
        # Fallback sans pyperclip via ctypes
        import ctypes
        ctypes.windll.user32.OpenClipboard(0)
        ctypes.windll.user32.EmptyClipboard()
        # Encoder en CF_UNICODETEXT
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = ctypes.windll.kernel32.GlobalAlloc(0x0042, len(data))
        ptr    = ctypes.windll.kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        ctypes.windll.kernel32.GlobalUnlock(handle)
        ctypes.windll.user32.SetClipboardData(13, handle)  # CF_UNICODETEXT = 13
        ctypes.windll.user32.CloseClipboard()


def _paste_windows() -> None:
    import pyautogui
    pyautogui.PAUSE = 0
    pyautogui.hotkey("ctrl", "v")


def _fallback_windows(text: str) -> None:
    """Frappe caractère par caractère via pyautogui — lent mais fiable."""
    try:
        import pyautogui
        # pyautogui.write() ne gère pas les accents →
        # on passe par le presse-papiers avec une méthode alternative
        import subprocess
        # Passer le texte via stdin pour éviter toute injection PowerShell
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
    except Exception as e:
        logger.error(f"inject_windows fallback PowerShell: {e}")
