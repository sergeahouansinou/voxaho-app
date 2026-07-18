"""
Détection de l'application active — socle des « profils par application » (Phase 3b).

Objectif : savoir quelle application est au premier plan pour appliquer
automatiquement des réglages de dictée différents selon le contexte (ex.
« dans VS Code → anglais, pas de reformatage IA ; dans Mail → français,
reformatage IA »). C'est le différenciateur maison : 100 % local, aucune donnée
ne quitte la machine.

Deux fonctions :
  - active_app()   : nom de l'app au premier plan (macOS / Windows), sinon None ;
  - match_profile(): résolution PURE d'un profil pour un nom d'app donné.

PRINCIPE ABSOLU : ce module ne doit JAMAIS lever. La dictée est le chemin
critique du produit — une détection d'app qui échoue (import manquant, API
plateforme indisponible, permission refusée…) doit simplement renvoyer None
et laisser la dictée retomber sur les défauts de la config. Tout est donc
enveloppé défensivement (try/except larges).
"""

import logging

logger = logging.getLogger(__name__)


# ── Détection de l'application au premier plan ───────────────────────────────

def active_app() -> str | None:
    """Nom de l'application actuellement au premier plan, ou None.

    - macOS : NSWorkspace.frontmostApplication().localizedName() (via AppKit) ;
    - Windows : fenêtre de premier plan → PID → nom d'exécutable (basename sans
      extension) via l'API Win32 (ctypes) ;
    - toute autre plateforme, ou tout échec (dépendance absente, permission
      refusée, API indisponible) → None.

    Ne LÈVE JAMAIS : chaque chemin plateforme est protégé, et un garde-fou
    ultime enveloppe l'aiguillage lui-même.
    """
    import sys

    try:
        if sys.platform == "darwin":
            return _active_app_macos()
        if sys.platform.startswith("win"):
            return _active_app_windows()
    except Exception as e:  # pragma: no cover - garde-fou ultime
        logger.debug("active_app a échoué: %s", e)
    # Plateforme non prise en charge (Linux…) ou échec → None.
    return None


def _active_app_macos() -> str | None:
    """Nom localisé de l'app frontale sur macOS, ou None (défensif)."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        name = app.localizedName()
        return str(name) if name else None
    except Exception as e:  # pragma: no cover - dépend de la plateforme
        logger.debug("active_app macOS indisponible: %s", e)
        return None


def _active_app_windows() -> str | None:
    """Nom de l'exécutable de la fenêtre frontale sur Windows, ou None (défensif).

    Chaîne d'appels Win32 : GetForegroundWindow → GetWindowThreadProcessId →
    OpenProcess → QueryFullProcessImageNameW. On renvoie le basename sans
    extension (ex. « Code.exe » → « Code ») pour matcher facilement un motif.
    """
    try:
        import os
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        # PROCESS_QUERY_LIMITED_INFORMATION (0x1000) : suffisant et le moins
        # privilégié pour QueryFullProcessImageNameW.
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not handle:
            return None
        try:
            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            )
            if not ok:
                return None
            full_path = buf.value
        finally:
            kernel32.CloseHandle(handle)

        if not full_path:
            return None
        base = os.path.basename(full_path)          # « Code.exe »
        name, _ext = os.path.splitext(base)         # « Code »
        return name or None
    except Exception as e:  # pragma: no cover - dépend de la plateforme
        logger.debug("active_app Windows indisponible: %s", e)
        return None


# ── Résolution PURE d'un profil ──────────────────────────────────────────────

def match_profile(app_name: str | None, profiles: list[dict]) -> dict | None:
    """Retourne le PREMIER profil dont le motif correspond à `app_name`, ou None.

    Fonction PURE (aucun effet de bord, aucune détection plateforme) → totalement
    testable sans Qt ni base.

    Règle de correspondance retenue : le motif du profil (`app_pattern`) est une
    SOUS-CHAÎNE (insensible à la casse) du nom de l'application (« pattern in
    app_name »). Autrement dit un motif « Code » matche « Visual Studio Code » et
    « VSCode ». Le sens inverse (app_name sous-chaîne du motif) n'est PAS retenu :
    un motif doit être plus court/générique que le nom complet de l'app.

    Cas limites :
      - app_name None → None (aucune app détectée) ;
      - liste vide → None ;
      - motif vide (après trim) → ignoré (ne matche jamais) ;
      - premier profil correspondant dans l'ordre de la liste → prioritaire.
    """
    if not app_name:
        return None
    haystack = app_name.lower()
    for profile in profiles:
        try:
            pattern = (profile.get("app_pattern") or "").strip().lower()
        except AttributeError:
            continue  # profil malformé (pas un dict) → ignoré défensivement
        if not pattern:
            continue
        if pattern in haystack:
            return profile
    return None
