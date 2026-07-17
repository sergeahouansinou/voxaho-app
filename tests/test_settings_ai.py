"""
Tests Phase 2B « Reformatage IA local » — volet UI (settings_window).

On ne crée AUCUNE QApplication ni QWidget : on teste la fonction pure
`ai_status_label` (import direct, sans instancier de widget) et on inspecte
la source via ast.parse pour vérifier la présence des contrôles, méthodes et
lectures/écritures de la clé de config `ai_reformat`.

Lancer : ./venv/bin/python -m pytest tests/test_settings_ai.py -v
"""

import ast
import sys
from pathlib import Path

# Racine du projet importable quel que soit le mode d'invocation de pytest.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SETTINGS_PATH = PROJECT_ROOT / "ui" / "settings_window.py"


# ── Outils d'analyse AST (headless, sans Qt) ──────────────────────────────────

def _source() -> str:
    return SETTINGS_PATH.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_source())


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    """Retourne le premier FunctionDef nommé `name` (méthode ou fonction)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _function_source(tree: ast.Module, name: str) -> str:
    node = _find_function(tree, name)
    assert node is not None, f"fonction/méthode {name!r} introuvable"
    seg = ast.get_source_segment(_source(), node)
    assert seg is not None
    return seg


# ── Fonction pure ai_status_label : 3 branches ────────────────────────────────

def test_ai_status_label_indisponible():
    """Composant absent → « Composant IA non installé »."""
    from ui.settings_window import ai_status_label
    assert ai_status_label(False, False) == "Composant IA non installé"
    # available=False prime, même si ready est True (état incohérent défensif).
    assert ai_status_label(False, True) == "Composant IA non installé"


def test_ai_status_label_a_telecharger():
    """Présent mais modèle absent → « Modèle IA non téléchargé »."""
    from ui.settings_window import ai_status_label
    assert ai_status_label(True, False) == "Modèle IA non téléchargé"


def test_ai_status_label_pret():
    """Présent + prêt → « ✓ Modèle IA prêt »."""
    from ui.settings_window import ai_status_label
    assert ai_status_label(True, True) == "✓ Modèle IA prêt"


def test_ai_status_label_trois_libelles_distincts():
    """Les trois états produisent trois libellés différents."""
    from ui.settings_window import ai_status_label
    libelles = {
        ai_status_label(False, False),
        ai_status_label(True, False),
        ai_status_label(True, True),
    }
    assert len(libelles) == 3


# ── Présence des contrôles et méthodes attendus ───────────────────────────────

def test_case_ck_ai_reformat_presente():
    """La case self.ck_ai_reformat est bien construite (QCheckBox)."""
    src = _source()
    assert "self.ck_ai_reformat" in src
    assert "QCheckBox" in src
    # Le libellé et le hint du contrat sont présents.
    assert "Qwen 2.5" in src
    assert "100 % local" in src


def test_methode_refresh_ai_status_existe():
    tree = _tree()
    assert _find_function(tree, "_refresh_ai_status") is not None


def test_methode_download_ai_model_existe():
    tree = _tree()
    assert _find_function(tree, "_download_ai_model") is not None


def test_worker_telechargement_ia_present():
    """Un worker QThread dédié au téléchargement IA existe avec signaux."""
    src = _source()
    assert "class _AiModelDownloader" in src
    assert "QThread" in src
    # Signaux success/error du pattern worker.
    assert "success = pyqtSignal()" in src
    assert "error   = pyqtSignal(str)" in src or "error = pyqtSignal(str)" in src
    # Le worker appelle bien le contrat llm.download_model dans son run().
    run_src = _function_source(_tree(), "run")  # 1er run() = celui du worker
    assert "download_model" in run_src


# ── Import défensif du composant IA (core.llm peut être absent) ───────────────

def test_import_llm_defensif():
    """core.llm est importé défensivement : absent → None, pas d'exception."""
    from ui.settings_window import _import_llm
    # Sur cette installation, core/llm.py peut être absent (agent parallèle).
    # L'appel ne doit jamais lever : il renvoie None ou un module valide.
    result = _import_llm()
    assert result is None or hasattr(result, "download_model")


# ── Trois états d'UI présents dans la source (libellés) ───────────────────────

def test_trois_etats_ui_libelles_presents():
    """Les libellés des 3 états (indispo / à télécharger / prêt) sont en source."""
    src = _source()
    assert "Composant IA non installé" in src
    assert "Modèle IA non téléchargé" in src
    assert "✓ Modèle IA prêt" in src
    # Bouton de téléchargement (~1 Go) présent.
    assert "Télécharger le modèle (~1 Go)" in src


# ── Lecture / écriture de la clé de config `ai_reformat` ──────────────────────

def test_load_values_lit_ai_reformat():
    """_load_values lit cfg['ai_reformat'] (défaut False) dans la case."""
    tree = _tree()
    body = _function_source(tree, "_load_values")
    assert 'cfg.get("ai_reformat", False)' in body
    assert "self.ck_ai_reformat.setChecked" in body


def test_collect_ecrit_ai_reformat():
    """_collect écrit cfg['ai_reformat'] = self.ck_ai_reformat.isChecked()."""
    tree = _tree()
    body = _function_source(tree, "_collect")
    assert 'cfg["ai_reformat"]' in body
    assert "self.ck_ai_reformat.isChecked()" in body


def test_ai_reformat_distinct_de_reformatting():
    """La nouvelle clé `ai_reformat` n'écrase pas l'ancienne `reformatting`."""
    tree = _tree()
    body = _function_source(tree, "_collect")
    assert 'cfg["reformatting"]' in body
    assert 'cfg["ai_reformat"]' in body


# ── Gate _building respecté (pas de preview pendant la construction) ──────────

def test_toggle_respecte_gate_building():
    """_on_ai_reformat_toggle sort tôt si self._building est vrai."""
    tree = _tree()
    body = _function_source(tree, "_on_ai_reformat_toggle")
    assert "self._building" in body
    assert "return" in body


# ── Validité syntaxique globale du module modifié ─────────────────────────────

def test_settings_window_syntaxe_valide():
    ast.parse(_source())  # lève SyntaxError si invalide
