"""
Tests Phase 0 « Vitesse foudroyante » — volet UI (settings_window / setup_wizard).

On ne crée AUCUNE QApplication ni QWidget : on teste des structures de données
et des constantes module-level, plus la validité syntaxique via ast.parse.
Lancer : ./venv/bin/python -m pytest tests/test_ui_phase0.py -v
"""

import ast
import os
import sys
from pathlib import Path

# Racine du projet importable quel que soit le mode d'invocation de pytest.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

UI_DIR = PROJECT_ROOT / "ui"


# ── Modèles Whisper (constante MODELS) ────────────────────────────────────────

def test_models_contient_large_v3_turbo():
    """large-v3-turbo est disponible dans la liste des modèles."""
    from ui.settings_window import MODELS
    codes = [code for code, _label, _desc in MODELS]
    assert "large-v3-turbo" in codes


def test_models_conserve_les_modeles_existants():
    """tiny / small / medium / large-v3 restent proposés."""
    from ui.settings_window import MODELS
    codes = {code for code, _label, _desc in MODELS}
    for attendu in ("tiny", "small", "medium", "large-v3"):
        assert attendu in codes, f"modèle {attendu!r} manquant"


def test_models_codes_uniques():
    """Aucun code modèle dupliqué (currentData sans ambiguïté)."""
    from ui.settings_window import MODELS
    codes = [code for code, _label, _desc in MODELS]
    assert len(codes) == len(set(codes)), f"codes dupliqués : {codes}"


def test_models_structure_triplets():
    """Chaque entrée est un triplet (code, libellé, description) de str."""
    from ui.settings_window import MODELS
    for entry in MODELS:
        assert len(entry) == 3
        code, label, desc = entry
        assert isinstance(code, str) and code
        assert isinstance(label, str) and label
        assert isinstance(desc, str) and desc


def test_turbo_est_le_modele_recommande():
    """La mention « recommandé » est portée par large-v3-turbo."""
    from ui.settings_window import MODELS
    by_code = {code: (label, desc) for code, label, desc in MODELS}
    label_turbo, _desc = by_code["large-v3-turbo"]
    assert "recommand" in label_turbo.lower()


# ── Mapping Vitesse / Qualité → beam_size (BEAM_PRESETS) ───────────────────────

def test_beam_presets_valeurs():
    """BEAM_PRESETS mappe speed→1 et quality→5."""
    from ui.settings_window import BEAM_PRESETS
    assert BEAM_PRESETS["speed"] == 1
    assert BEAM_PRESETS["quality"] == 5


def test_beam_presets_types_et_ordre():
    """Valeurs entières et vitesse < qualité (beam croissant)."""
    from ui.settings_window import BEAM_PRESETS
    assert all(isinstance(v, int) for v in BEAM_PRESETS.values())
    assert BEAM_PRESETS["speed"] < BEAM_PRESETS["quality"]


# ── Validité syntaxique des modules UI (sans import Qt) ────────────────────────

def _assert_parses(filename: str):
    path = UI_DIR / filename
    src = path.read_text(encoding="utf-8")
    ast.parse(src)  # lève SyntaxError si invalide


def test_settings_window_syntaxe_valide():
    _assert_parses("settings_window.py")


def test_setup_wizard_syntaxe_valide():
    _assert_parses("setup_wizard.py")


# ── Cohérence entre le wizard et la liste MODELS ──────────────────────────────

def test_wizard_propose_large_v3_turbo():
    """Le wizard (setup_wizard.py) propose bien le code large-v3-turbo.

    Vérifié sur la source (pas d'instanciation de widget) pour rester headless.
    """
    src = (UI_DIR / "setup_wizard.py").read_text(encoding="utf-8")
    assert '"large-v3-turbo"' in src


def test_wizard_config_draft_defaults_phase0():
    """Le brouillon de config du wizard porte beam_size=1 et input_device=None.

    On lit ces valeurs par analyse AST du littéral _config_draft, sans Qt.
    """
    src = (UI_DIR / "setup_wizard.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    draft = None
    for node in ast.walk(tree):
        # self._config_draft = {...}  →  Assign ; annoté ( : dict) →  AnnAssign.
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if (isinstance(target, ast.Attribute)
                    and target.attr == "_config_draft"
                    and isinstance(node.value, ast.Dict)):
                draft = node.value
    assert draft is not None, "_config_draft introuvable dans setup_wizard.py"

    literals = {}
    for key_node, val_node in zip(draft.keys, draft.values):
        if isinstance(key_node, ast.Constant):
            try:
                literals[key_node.value] = ast.literal_eval(val_node)
            except (ValueError, SyntaxError):
                literals[key_node.value] = None
    assert literals.get("beam_size") == 1
    assert literals.get("input_device") is None
