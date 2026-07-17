"""
Tests Phase 2 « Intelligence locale » — ouverture des langues.

On ne crée AUCUNE QApplication ni QWidget : on teste uniquement la constante
LANGS (structure de données module-level) et sa cohérence entre les Préférences
(ui.settings_window) et le wizard (ui.setup_wizard).
Lancer : ./venv/bin/python -m pytest tests/test_languages.py -v
"""

import sys
from pathlib import Path

# Racine du projet importable quel que soit le mode d'invocation de pytest.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ui.settings_window import LANGS

# Langues prioritaires attendues en tête, dans cet ordre exact.
PRIORITY = ["fr", "en", "es", "de", "it"]


# ── Taille de l'offre ─────────────────────────────────────────────────────────

def test_langs_offre_elargie():
    """L'offre de langues est largement élargie (>= 30 entrées)."""
    assert len(LANGS) >= 30, f"seulement {len(LANGS)} langues"


# ── Structure des entrées ──────────────────────────────────────────────────────

def test_langs_structure_tuples():
    """Chaque entrée est un tuple (code, label) de deux str non vides."""
    for entry in LANGS:
        assert isinstance(entry, tuple), f"entrée non-tuple : {entry!r}"
        assert len(entry) == 2, f"entrée != 2 éléments : {entry!r}"
        code, label = entry
        assert isinstance(code, str), f"code non-str : {code!r}"
        assert isinstance(label, str), f"label non-str : {label!r}"


def test_langs_codes_non_vides():
    """Aucun code n'est vide ou None."""
    for code, _label in LANGS:
        assert code, f"code vide/None : {code!r}"
        assert code is not None


def test_langs_labels_drapeau_plus_nom():
    """Chaque label est non vide et contient un espace (drapeau + nom natif)."""
    for _code, label in LANGS:
        assert label, f"label vide : {label!r}"
        assert " " in label, f"label sans espace (drapeau + nom attendu) : {label!r}"


# ── Unicité des codes ──────────────────────────────────────────────────────────

def test_langs_codes_uniques():
    """Aucun code langue dupliqué (currentData sans ambiguïté)."""
    codes = [code for code, _label in LANGS]
    assert len(codes) == len(set(codes)), f"codes dupliqués : {codes}"


# ── Langues prioritaires en tête ───────────────────────────────────────────────

def test_langs_priorite_en_tete():
    """fr / en / es / de / it apparaissent en tête, dans cet ordre exact."""
    head = [code for code, _label in LANGS[: len(PRIORITY)]]
    assert head == PRIORITY, f"tête inattendue : {head}"


# ── « Auto » en dernier et unique ──────────────────────────────────────────────

def test_langs_auto_en_dernier():
    """L'entrée « auto » (détection automatique) est la toute dernière."""
    assert LANGS[-1][0] == "auto", f"dernière entrée : {LANGS[-1]!r}"


def test_langs_auto_unique():
    """« auto » n'apparaît qu'une seule fois."""
    codes = [code for code, _label in LANGS]
    assert codes.count("auto") == 1, f"« auto » présent {codes.count('auto')} fois"


# ── Cohérence wizard ↔ préférences ─────────────────────────────────────────────

def test_wizard_reutilise_les_memes_langs():
    """Le wizard partage exactement la même liste LANGS (source unique)."""
    from ui.setup_wizard import LANGS as W
    assert W == LANGS, "les LANGS du wizard divergent de celles des préférences"
