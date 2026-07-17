"""
Tests de la version centralisée et des helpers purs.

Aucune QApplication n'est créée : on importe les modules (les imports Qt
au niveau module sont inoffensifs) et on teste des fonctions pures.
Lancer : ./venv/bin/python -m pytest tests/test_version.py -v
"""

import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Version unique (core/__init__.py) ────────────────────────────────────────

def test_version_importable_et_semver():
    """`from core import __version__` fonctionne et matche le pattern semver."""
    from core import __version__
    assert isinstance(__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+$", __version__), (
        f"__version__ = {__version__!r} n'est pas au format semver X.Y.Z"
    )


def test_setup_py_utilise_version_centralisee():
    """setup.py référence core.__version__ — plus de version en dur."""
    with open(os.path.join(PROJECT_ROOT, "setup.py"), encoding="utf-8") as f:
        src = f.read()
    # La référence à la source unique est présente…
    assert "from core import __version__" in src
    assert re.search(r"VERSION\s*=\s*__version__", src)
    # …et il n'y a plus de version littérale en dur type VERSION = '1.0.x'
    assert not re.search(r"VERSION\s*=\s*['\"]\d+\.\d+\.\d+['\"]", src), (
        "setup.py contient encore une version en dur (VERSION = '1.0.x')"
    )


def test_settings_window_expose_la_version_centralisee():
    """settings_window importe la version depuis core (pas de doublon)."""
    from core import __version__
    import ui.settings_window as sw
    assert sw.__version__ == __version__


# ── _model_is_cached (fonction pure, sans Qt) ────────────────────────────────

def test_model_is_cached_faux_puis_vrai(tmp_path, monkeypatch):
    """Détecte l'existence du dossier de cache HuggingFace du modèle."""
    # Redirige le home vers tmp_path (expanduser lit HOME sur POSIX)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows

    from ui.settings_window import _model_is_cached

    # Aucun cache → False
    assert _model_is_cached("small") is False
    assert _model_is_cached("large-v3") is False

    # Crée le dossier factice du modèle small → True (et small uniquement)
    cache = (tmp_path / ".cache" / "huggingface" / "hub"
             / "models--Systran--faster-whisper-small")
    cache.mkdir(parents=True)
    assert _model_is_cached("small") is True
    assert _model_is_cached("large-v3") is False

    # Idem pour large-v3
    (tmp_path / ".cache" / "huggingface" / "hub"
     / "models--Systran--faster-whisper-large-v3").mkdir(parents=True)
    assert _model_is_cached("large-v3") is True
