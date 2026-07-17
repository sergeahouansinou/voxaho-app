"""
Injection du texte transcrit dans l'application active.

  macOS   → pbcopy + Cmd+V  |  fallback : osascript keystroke
  Windows → pyperclip + Ctrl+V  |  fallback : pyautogui.write()

Le presse-papiers de l'utilisateur est sauvegardé AVANT l'injection puis
restauré APRÈS le collage : la dictée ne doit pas écraser ce que
l'utilisateur avait copié (lien, texte, mot de passe…).
"""

import sys
import time
import logging
import subprocess

logger = logging.getLogger(__name__)

IS_MAC   = sys.platform == "darwin"
DELAY    = 0.15   # délai entre copie et collage

# Délai avant restauration du presse-papiers : laisser l'app cible consommer
# le collage (Cmd+V / Ctrl+V est traité de façon asynchrone par l'app) avant
# de remettre l'ancien contenu — sinon c'est l'ancien texte qui serait collé
# à la place de la transcription.
RESTORE_DELAY = 0.3


def inject_text(text: str) -> None:
    """Injecte `text` dans l'application active.

    Ordre des opérations (vérifié par tests/test_injector.py) :
      1. sauvegarde du texte actuel du presse-papiers ;
      2. écriture de la transcription + collage (logique historique) ;
      3. restauration de l'ancien contenu après RESTORE_DELAY.

    Appelée depuis un thread non-GUI : aucune dépendance Qt ici, et aucun
    état partagé mutable (thread-safe). NSPasteboard depuis un thread
    secondaire est acceptable.
    """
    if not text:
        return

    # 1. Sauvegarder l'ancien presse-papiers — ne bloque jamais l'injection
    saved = _save_clipboard()

    # 2. Écrire la transcription et coller (logique existante inchangée)
    if IS_MAC:
        _inject_mac(text)
    else:
        _inject_windows(text)

    # 3. Restaurer l'ancien contenu — ne bloque jamais l'injection
    _restore_clipboard(saved)


# ── Sauvegarde / restauration du presse-papiers ────────────────────────────

def _save_clipboard() -> str | None:
    """Retourne le texte actuel du presse-papiers, ou None si vide/non-texte.

    Toute erreur est loggée en warning : la sauvegarde ne doit JAMAIS faire
    échouer l'injection elle-même.
    """
    try:
        if IS_MAC:
            return _read_clipboard_mac()
        return _read_clipboard_windows()
    except Exception as e:
        logger.warning(f"Sauvegarde du presse-papiers échouée: {e}")
        return None


def _restore_clipboard(saved: str | None) -> None:
    """Restaure `saved` dans le presse-papiers après le collage.

    - `saved` vide ou None → rien à restaurer : on n'écrase pas la
      transcription avec une chaîne vide (laisser la transcription dans le
      presse-papiers est alors un comportement acceptable).
    - Toute erreur est loggée en warning, jamais propagée : la restauration
      ne doit JAMAIS faire échouer l'injection elle-même.
    """
    if not saved:
        logger.debug("Presse-papiers initial vide ou non-texte : pas de restauration")
        return
    try:
        # Laisser l'app cible consommer le collage avant de restaurer
        time.sleep(RESTORE_DELAY)
        if IS_MAC:
            _clipboard_mac(saved)
        else:
            _clipboard_windows(saved)
    except Exception as e:
        logger.warning(f"Restauration du presse-papiers échouée: {e}")


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
    """Copie dans le presse-papiers macOS via NSPasteboard (API native, zéro encodage).

    pbcopy en fallback si AppKit indisponible. Sans LANG=UTF-8 dans l'env du
    process py2app, pbcopy peut convertir les bytes UTF-8 en Mac Roman →
    mojibake ("é" devient "√©"). NSPasteboard évite complètement ce piège.
    """
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        ok = pb.setString_forType_(text, NSPasteboardTypeString)
        if not ok:
            raise RuntimeError("NSPasteboard.setString_forType_ retourné False")
    except ImportError:
        # Fallback : pbcopy avec LANG forcé en UTF-8 pour éviter le mojibake
        import os
        env = os.environ.copy()
        env["LANG"] = env.get("LANG") or "en_US.UTF-8"
        env["LC_ALL"] = env.get("LC_ALL") or "en_US.UTF-8"
        proc = subprocess.run(
            ["pbcopy"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=3,
            env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"pbcopy: {proc.stderr.decode(errors='replace')}")


def _read_clipboard_mac() -> str | None:
    """Lit le contenu TEXTE actuel du presse-papiers macOS via NSPasteboard.

    Limitation : seul le texte peut être sauvegardé puis restauré. Si le
    presse-papiers contient autre chose (image, fichiers…), il n'y a rien à
    récupérer côté texte → retourne None et la restauration est sautée
    (loggé en debug).

    pbpaste en fallback si AppKit indisponible — symétrique du fallback
    pbcopy de _clipboard_mac, avec le même LANG forcé UTF-8 anti-mojibake.
    """
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        content = pb.stringForType_(NSPasteboardTypeString)
        if content is None and pb.types():
            # Contenu non-texte (image, fichiers…) : restauration impossible
            logger.debug("Presse-papiers non-texte : sauvegarde texte impossible")
        return content
    except ImportError:
        import os
        env = os.environ.copy()
        env["LANG"] = env.get("LANG") or "en_US.UTF-8"
        env["LC_ALL"] = env.get("LC_ALL") or "en_US.UTF-8"
        proc = subprocess.run(["pbpaste"], capture_output=True, timeout=3, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"pbpaste: {proc.stderr.decode(errors='replace')}")
        return proc.stdout.decode("utf-8", errors="replace") or None


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


def _read_clipboard_windows() -> str | None:
    """Lit le contenu TEXTE actuel du presse-papiers Windows via pyperclip.

    Limitation : seul le texte peut être sauvegardé puis restauré. Si le
    presse-papiers contient autre chose (image, fichiers…), il n'y a rien à
    récupérer côté texte → retourne None et la restauration est sautée
    (loggé en debug).

    Fallback ctypes GetClipboardData si pyperclip absent — symétrique du
    fallback ctypes d'écriture de _clipboard_windows.
    """
    try:
        import pyperclip
        return pyperclip.paste() or None
    except ImportError:
        import ctypes
        CF_UNICODETEXT = 13
        user32   = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            raise RuntimeError("OpenClipboard a échoué")
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                # Vide ou contenu non-texte : restauration impossible
                logger.debug("Presse-papiers non-texte : sauvegarde texte impossible")
                return None
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr) or None
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()


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
