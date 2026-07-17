"""
Tests de la fenêtre principale (ui/workspace_window.py) — PHASE 1 « Le Workspace ».

Aucune QApplication ni QWidget n'est instancié : on teste les fonctions PURES
extraites au niveau module (format_iso_datetime, truncate_text, format_minutes),
la validité syntaxique (ast.parse) et la présence de la classe WorkspaceWindow
via un simple import du module (qui ne doit construire AUCUN widget).

Lancer : ./venv/bin/python -m pytest tests/test_workspace.py -v
"""

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MODULE_PATH = PROJECT_ROOT / "ui" / "workspace_window.py"


# ── Validité syntaxique (sans import Qt) ──────────────────────────────────────

def test_module_syntaxe_valide():
    """Le fichier se parse sans SyntaxError."""
    src = MODULE_PATH.read_text(encoding="utf-8")
    ast.parse(src)  # lève SyntaxError si invalide


# ── Import du module + présence de la classe ──────────────────────────────────
# Note : l'import déclenche PyQt6 (imports module-level) mais NE construit aucun
# widget — c'est acceptable sur cette machine (PyQt6 est installé).

def _import_module():
    try:
        import ui.workspace_window as w
    except ImportError as e:  # PyQt6 absent : on saute proprement
        pytest.skip(f"PyQt6 indisponible : {e}")
    return w


def test_workspace_window_existe():
    w = _import_module()
    assert hasattr(w, "WorkspaceWindow"), "classe WorkspaceWindow manquante"


def test_fonctions_pures_exportees():
    w = _import_module()
    assert callable(w.format_iso_datetime)
    assert callable(w.truncate_text)
    assert callable(w.format_minutes)


# ── format_iso_datetime ───────────────────────────────────────────────────────

def test_format_iso_datetime_basique():
    w = _import_module()
    assert w.format_iso_datetime("2026-07-17T14:32:05") == "17 juil. 14:32"


def test_format_iso_datetime_janvier_pad_heure():
    w = _import_module()
    # 1er janvier à 09:05 → mois « janv. » et heure zéro-paddée.
    assert w.format_iso_datetime("2026-01-01T09:05:00") == "1 janv. 09:05"


def test_format_iso_datetime_suffixe_z():
    w = _import_module()
    # Le « Z » (UTC) est toléré ; l'heure affichée reste celle du timestamp.
    assert w.format_iso_datetime("2026-12-25T23:07:00Z") == "25 déc. 23:07"


def test_format_iso_datetime_date_seule():
    w = _import_module()
    # Date sans heure → 00:00.
    assert w.format_iso_datetime("2026-03-08") == "8 mars 00:00"


def test_format_iso_datetime_vide_et_invalide():
    w = _import_module()
    assert w.format_iso_datetime("") == ""
    assert w.format_iso_datetime(None) == ""
    # Non parsable → renvoie la chaîne d'origine (pas de crash).
    assert w.format_iso_datetime("pas une date") == "pas une date"


def test_format_iso_datetime_tous_les_mois():
    w = _import_module()
    attendus = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
                "juil.", "août", "sept.", "oct.", "nov.", "déc."]
    for i, mois in enumerate(attendus, start=1):
        iso = f"2026-{i:02d}-15T10:00:00"
        assert w.format_iso_datetime(iso) == f"15 {mois} 10:00"


# ── truncate_text ─────────────────────────────────────────────────────────────

def test_truncate_text_sous_la_limite():
    w = _import_module()
    assert w.truncate_text("bonjour", 80) == "bonjour"


def test_truncate_text_au_dessus_de_la_limite():
    w = _import_module()
    res = w.truncate_text("abcdefghij", 5)
    assert res.endswith("…")
    assert len(res) == 5  # 4 caractères + ellipse


def test_truncate_text_normalise_espaces():
    w = _import_module()
    assert w.truncate_text("a\n  b\t c", 80) == "a b c"


def test_truncate_text_none_et_bornes():
    w = _import_module()
    assert w.truncate_text(None) == ""
    assert w.truncate_text("quoi que ce soit", 0) == ""


# ── format_minutes ────────────────────────────────────────────────────────────

def test_format_minutes_sous_une_heure():
    w = _import_module()
    assert w.format_minutes(45) == "45 min"
    assert w.format_minutes(0) == "0 min"


def test_format_minutes_heures_pleines():
    w = _import_module()
    assert w.format_minutes(120) == "2 h"


def test_format_minutes_heures_et_minutes():
    w = _import_module()
    assert w.format_minutes(200) == "3 h 20"


def test_format_minutes_robuste():
    w = _import_module()
    assert w.format_minutes(None) == "0 min"
    assert w.format_minutes("bof") == "0 min"
    assert w.format_minutes(-10) == "0 min"


# ── L'import ne doit PAS instancier de widget ─────────────────────────────────

def test_import_ne_construit_pas_de_widget():
    """WorkspaceWindow est une classe, pas une instance ; l'import reste inerte.

    Vérifié par analyse AST : aucun appel top-level à QApplication(...) ou
    WorkspaceWindow(...) dans le corps du module.
    """
    src = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    interdits = {"QApplication", "WorkspaceWindow", "QMainWindow"}
    for node in tree.body:  # uniquement le niveau module (pas dans les méthodes)
        for sub in ast.walk(node) if isinstance(node, (ast.If, ast.Expr, ast.Assign)) else []:
            if isinstance(sub, ast.Call):
                fn = sub.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                assert name not in interdits, f"instanciation top-level interdite : {name}"
