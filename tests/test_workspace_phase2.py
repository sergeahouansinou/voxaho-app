"""
Tests du Workspace — PHASE 2 « Intelligence locale » (Dictionnaire + Snippets).

Aucune QApplication ni QWidget n'est instancié : on vérifie via ast.parse +
lecture du source la présence des 2 nouvelles sections (Dictionnaire, Snippets)
dans la nav, l'ordre nav ⇄ stack, les appels défensifs au moteur
(dictionary.add_term / list_terms, snippets.add_snippet / list_snippets), et on
teste la fonction PURE snippet_preview (importable sans Qt via ast, mais aussi
directement puisqu'elle ne dépend pas de PyQt6).

Lancer : ./venv/bin/python -m pytest tests/test_workspace_phase2.py -v
"""

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MODULE_PATH = PROJECT_ROOT / "ui" / "workspace_window.py"


def _source() -> str:
    return MODULE_PATH.read_text(encoding="utf-8")


def _import_module():
    try:
        import ui.workspace_window as w
    except ImportError as e:  # PyQt6 absent : on saute proprement
        pytest.skip(f"PyQt6 indisponible : {e}")
    return w


# ── Validité syntaxique (sans import Qt) ──────────────────────────────────────

def test_module_syntaxe_valide():
    """Le fichier se parse toujours sans SyntaxError après l'ajout des sections."""
    ast.parse(_source())


# ── Présence des 2 nouvelles sections dans la navigation ──────────────────────

def test_nav_contient_dictionnaire_et_snippets():
    """Les libellés « Dictionnaire » et « Snippets » figurent dans le source."""
    src = _source()
    assert "Dictionnaire" in src, "section Dictionnaire manquante"
    assert "Snippets" in src, "section Snippets manquante"


def test_nav_ordre_notes_dictionnaire_snippets_statistiques():
    """Dans la liste des libellés nav, l'ordre est Notes → Dictionnaire →
    Snippets → Statistiques (insertion après Notes, avant Statistiques)."""
    src = _source()
    i_notes = src.index("📝  Notes")
    i_dict = src.index("📖  Dictionnaire")
    i_snip = src.index("⚡  Snippets")
    i_stats = src.index("📊  Statistiques")
    assert i_notes < i_dict < i_snip < i_stats


# ── Ordre nav ⇄ stack : les pages sont ajoutées dans le même ordre ────────────

def test_ordre_stack_synchronise():
    """Les addWidget du QStackedWidget suivent l'ordre de la nav :
    dashboard, history, notes, dictionary, snippets, stats."""
    src = _source()
    ordre_attendu = [
        "_build_dashboard_page",
        "_build_history_page",
        "_build_notes_page",
        "_build_dictionary_page",
        "_build_snippets_page",
        "_build_stats_page",
    ]
    positions = [src.index(f"self.stack.addWidget(self.{name}(") for name in ordre_attendu]
    assert positions == sorted(positions), "ordre d'ajout au stack non synchronisé avec la nav"


def test_constantes_index_pages_a_jour():
    """Les constantes PAGE_* reflètent le décalage (Statistiques passe à 5)."""
    w = _import_module()
    assert w.WorkspaceWindow.PAGE_NOTES == 2
    assert w.WorkspaceWindow.PAGE_DICTIONARY == 3
    assert w.WorkspaceWindow.PAGE_SNIPPETS == 4
    assert w.WorkspaceWindow.PAGE_STATS == 5


# ── Appels défensifs au moteur (via ast : recherche des attributs appelés) ────

def _appels_safe_call(src: str) -> set[tuple[str, str]]:
    """Retourne l'ensemble des couples (module, fonction) passés à _safe_call.

    _safe_call(mod_name, func_name, ...) : on collecte les 2 premiers arguments
    positionnels quand ce sont des littéraux chaînes.
    """
    tree = ast.parse(src)
    couples = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if getattr(fn, "attr", None) != "_safe_call":
            continue
        args = node.args
        if len(args) >= 2 and isinstance(args[0], ast.Constant) and isinstance(args[1], ast.Constant):
            couples.add((args[0].value, args[1].value))
    return couples


def test_appels_moteur_dictionnaire():
    couples = _appels_safe_call(_source())
    assert ("dictionary", "add_term") in couples
    assert ("dictionary", "list_terms") in couples
    assert ("dictionary", "remove_term") in couples


def test_appels_moteur_snippets():
    couples = _appels_safe_call(_source())
    assert ("snippets", "add_snippet") in couples
    assert ("snippets", "list_snippets") in couples
    assert ("snippets", "update_snippet") in couples
    assert ("snippets", "remove_snippet") in couples


# ── Fonction PURE snippet_preview ─────────────────────────────────────────────

def test_snippet_preview_existe():
    w = _import_module()
    assert callable(w.snippet_preview)


def test_snippet_preview_basique():
    w = _import_module()
    assert w.snippet_preview("adr", "12 rue des Lilas") == "adr → 12 rue des Lilas"


def test_snippet_preview_tronque_expansion():
    w = _import_module()
    res = w.snippet_preview("sig", "a" * 100, n=10)
    # trigger conservé, flèche présente, expansion tronquée avec ellipse.
    assert res.startswith("sig → ")
    assert res.endswith("…")


def test_snippet_preview_normalise_declencheur():
    w = _import_module()
    assert w.snippet_preview("  mon   nom  ", "Voxaho") == "mon nom → Voxaho"


def test_snippet_preview_champs_vides():
    w = _import_module()
    assert w.snippet_preview("", "") == ""
    assert w.snippet_preview(None, None) == ""


# ── Présence des méthodes de construction/rafraîchissement ────────────────────

def test_methodes_pages_presentes():
    w = _import_module()
    for m in ("_build_dictionary_page", "_refresh_dictionary",
              "_build_snippets_page", "_refresh_snippets",
              "_on_add_term", "_delete_term",
              "_on_save_snippet", "_edit_snippet", "_delete_snippet"):
        assert hasattr(w.WorkspaceWindow, m), f"méthode manquante : {m}"


def test_refresh_gere_nouvelles_pages():
    """refresh() aiguille vers _refresh_dictionary et _refresh_snippets."""
    src = _source()
    assert "self.PAGE_DICTIONARY" in src
    assert "self.PAGE_SNIPPETS" in src
    assert "self._refresh_dictionary()" in src
    assert "self._refresh_snippets()" in src


# ── États vides (« Aucun terme » / « Aucun snippet ») ─────────────────────────

def test_etats_vides_presents():
    src = _source()
    assert "Aucun terme" in src
    assert "Aucun snippet" in src
