"""
Détection matérielle locale + recommandation de modèle Whisper.

100 % local, sans dépendance externe (stdlib uniquement). Tout est tolérant
aux erreurs : la détection ne lève JAMAIS et retombe sur des valeurs par
défaut raisonnables. `recommend_model()` est une fonction PURE et testable
(aucun effet de bord, aucun accès système) : elle ne dépend que du dict de
specs qu'on lui passe.

Utilisé par le wizard de premier lancement (ui/setup_wizard.py) pour :
  - afficher un résumé des specs (cœurs, RAM, Apple Silicon) ;
  - pré-sélectionner le meilleur modèle possible avec sa justification.
"""

import os
import sys
import platform
import importlib.util
import logging

logger = logging.getLogger(__name__)

# Modèles valides (cohérents avec ui.settings_window.MODELS).
VALID_MODELS = ("tiny", "small", "medium", "large-v3", "large-v3-turbo")

# RAM de repli quand la détection est impossible (valeur médiane prudente).
_DEFAULT_RAM_GB = 8.0


# ── Détection ────────────────────────────────────────────────────────────────

def _detect_ram_gb() -> float:
    """RAM physique totale en Go (base décimale, 1e9). Tolérant aux erreurs.

    macOS / Linux : via os.sysconf (SC_PAGE_SIZE × SC_PHYS_PAGES).
    Windows       : via ctypes GlobalMemoryStatusEx.
    Indéterminable : repli sur _DEFAULT_RAM_GB.
    """
    # macOS / Linux — POSIX sysconf.
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        if page_size > 0 and phys_pages > 0:
            return float(page_size * phys_pages) / 1e9
    except (ValueError, AttributeError, OSError):
        pass  # SC_* absent (Windows) ou indisponible → on tente la voie ctypes.

    # Windows — GlobalMemoryStatusEx.
    if sys.platform.startswith("win"):
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return float(stat.ullTotalPhys) / 1e9
        except Exception as e:  # ctypes indisponible / appel échoué
            logger.debug("GlobalMemoryStatusEx indisponible : %s", e)

    return _DEFAULT_RAM_GB


def _mlx_whisper_available() -> bool:
    """True si le module `mlx_whisper` est importable (sans l'importer réellement)."""
    try:
        return importlib.util.find_spec("mlx_whisper") is not None
    except Exception:
        return False


def detect_hardware() -> dict:
    """Détecte les caractéristiques matérielles utiles au choix du modèle.

    Ne LÈVE JAMAIS : chaque champ est protégé et retombe sur une valeur par
    défaut raisonnable. Renvoie un dict avec les clés :
      - platform      : sys.platform (str)
      - is_arm64      : bool (Apple Silicon / ARM64)
      - cpu_count     : int >= 1
      - ram_gb        : float (Go, base décimale)
      - has_metal     : bool (macOS + arm64 → GPU Metal présumé disponible)
      - mlx_available : bool (module mlx_whisper importable)
    """
    try:
        plat = sys.platform
    except Exception:
        plat = "unknown"

    try:
        is_arm64 = platform.machine() in ("arm64", "aarch64")
    except Exception:
        is_arm64 = False

    try:
        cpu_count = os.cpu_count() or 1
    except Exception:
        cpu_count = 1

    try:
        ram_gb = _detect_ram_gb()
    except Exception as e:  # pragma: no cover - _detect_ram_gb est déjà tolérant
        logger.debug("Détection RAM impossible : %s", e)
        ram_gb = _DEFAULT_RAM_GB

    has_metal = (plat == "darwin") and is_arm64

    try:
        mlx_available = _mlx_whisper_available()
    except Exception:
        mlx_available = False

    return {
        "platform": plat,
        "is_arm64": bool(is_arm64),
        "cpu_count": int(cpu_count),
        "ram_gb": float(ram_gb),
        "has_metal": bool(has_metal),
        "mlx_available": bool(mlx_available),
    }


# ── Recommandation (fonction PURE) ───────────────────────────────────────────

def recommend_model(hw: dict) -> dict:
    """Recommande un modèle Whisper à partir des specs détectées.

    Fonction PURE : ne lit que `hw`, aucun effet de bord, aucun accès système.
    Tolérante aux dicts vides ou partiels (valeurs manquantes → défauts sûrs).

    Renvoie {"model", "compute_backend", "beam_size", "reason"} où :
      - model           ∈ VALID_MODELS
      - compute_backend ∈ {"auto", "cpu", "mlx"}
      - beam_size       : int >= 1 (1 = greedy, le plus rapide)
      - reason          : phrase FR expliquant le choix (jamais vide)

    Heuristique DOCUMENTÉE (du plus contraint au plus capable) :
      1. RAM < 8 Go OU CPU <= 2 cœurs  → tiny  (rester fluide sur machine modeste)
      2. RAM >= 16 Go ET Apple Silicon → large-v3-turbo / backend auto (Metal/MLX
                                          si dispo, sinon CPU) → qualité maximale
      3. RAM >= 16 Go (sans ARM64)     → medium (bon équilibre sur bon CPU)
      4. RAM >= 8 Go                   → small  (léger et rapide, cas général)
    """
    hw = hw if isinstance(hw, dict) else {}

    # Extraction défensive : specs partielles → valeurs par défaut prudentes.
    try:
        ram_gb = float(hw.get("ram_gb", _DEFAULT_RAM_GB))
    except (TypeError, ValueError):
        ram_gb = _DEFAULT_RAM_GB
    try:
        cpu_count = int(hw.get("cpu_count", 1))
    except (TypeError, ValueError):
        cpu_count = 1
    is_arm64 = bool(hw.get("is_arm64", False))

    # 1. Machine modeste (peu de RAM ou peu de cœurs) → modèle minimal.
    if ram_gb < 8 or cpu_count <= 2:
        return {
            "model": "tiny",
            "compute_backend": "cpu",
            "beam_size": 1,
            "reason": "Ressources limitées → modèle minimal (tiny) pour rester fluide.",
        }

    # 2. Machine puissante + Apple Silicon → qualité quasi-maximale accélérée.
    if ram_gb >= 16 and is_arm64:
        return {
            "model": "large-v3-turbo",
            "compute_backend": "auto",
            "beam_size": 1,
            "reason": "16 Go + Apple Silicon → qualité maximale possible (large-v3-turbo accéléré).",
        }

    # 3. Beaucoup de RAM sans Apple Silicon → bon compromis sur CPU costaud.
    if ram_gb >= 16:
        return {
            "model": "medium",
            "compute_backend": "cpu",
            "beam_size": 1,
            "reason": "16 Go sans Apple Silicon → bon équilibre qualité/vitesse (medium sur CPU).",
        }

    # 4. Cas général (8–16 Go) → modèle léger et rapide.
    return {
        "model": "small",
        "compute_backend": "cpu",
        "beam_size": 1,
        "reason": "8 Go de RAM → modèle léger et rapide (small), idéal au quotidien.",
    }


# ── Résumé lisible (fonction PURE) ───────────────────────────────────────────

def specs_summary(hw: dict) -> str:
    """Résumé court et lisible des specs, pour l'affichage dans le wizard.

    Fonction PURE (aucun effet de bord). Exemple :
    « 10 cœurs · 16 Go RAM · Apple Silicon ». Tolérante aux dicts partiels.
    """
    hw = hw if isinstance(hw, dict) else {}

    try:
        cpu = int(hw.get("cpu_count", 1))
    except (TypeError, ValueError):
        cpu = 1
    try:
        ram = float(hw.get("ram_gb", _DEFAULT_RAM_GB))
    except (TypeError, ValueError):
        ram = _DEFAULT_RAM_GB
    is_arm64 = bool(hw.get("is_arm64", False))

    coeur = "cœur" if cpu <= 1 else "cœurs"
    chip = "Apple Silicon" if is_arm64 else "CPU x86"
    return f"{cpu} {coeur} · {ram:.0f} Go RAM · {chip}"
