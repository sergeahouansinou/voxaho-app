"""
Tests Phase 3b — refonte du wizard (ui/setup_wizard.py).

SANS instancier QApplication ni le moindre QWidget : on valide la source via
ast.parse et on vérifie la présence des points d'intégration de la refonte
(détection matérielle, sélection micro, option IA, dictée d'essai) directement
sur l'arbre syntaxique / le texte source.
Lancer : ./venv/bin/python -m pytest tests/test_wizard_refonte.py -v
"""

import ast
import sys
from pathlib import Path

# Racine du projet importable quel que soit le mode d'invocation de pytest.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

WIZARD_PATH = PROJECT_ROOT / "ui" / "setup_wizard.py"
SRC = WIZARD_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SRC)  # lève SyntaxError si le module est invalide


# ── Validité syntaxique ───────────────────────────────────────────────────────

def test_wizard_syntaxe_valide():
    """Le module se parse sans SyntaxError (déjà fait au chargement)."""
    assert isinstance(TREE, ast.Module)


# ── Intégration détection matérielle ──────────────────────────────────────────

def test_import_core_hardware_present():
    """Le wizard référence core.hardware (détection + reco)."""
    assert "core import hardware" in SRC or "core.hardware" in SRC


def test_appelle_detect_et_recommend():
    """Les deux entrées de core.hardware sont utilisées."""
    assert "detect_hardware(" in SRC
    assert "recommend_model(" in SRC


def test_methode_select_recommended_model():
    """Une méthode pré-sélectionne le modèle recommandé dans le combo."""
    noms = {n.name for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)}
    assert "_select_recommended_model" in noms


def test_bouton_utiliser_recommandation():
    """Le libellé du bouton « Utiliser la recommandation » est présent."""
    assert "Utiliser la recommandation" in SRC


# ── Alignement des modèles ────────────────────────────────────────────────────

def test_combo_modele_contient_turbo():
    """Le combo modèle propose large-v3-turbo (aligné sur settings.MODELS)."""
    assert '"large-v3-turbo"' in SRC


# ── Sélection du micro dans le wizard ─────────────────────────────────────────

def test_import_list_input_devices():
    """Le wizard importe list_input_devices depuis core.recorder."""
    assert "list_input_devices" in SRC


def test_combo_micro_present():
    """Un combo micro est construit dans le wizard."""
    assert "_mic_combo" in SRC
    assert "Micro système par défaut" in SRC


# ── Option reformatage IA ─────────────────────────────────────────────────────

def test_case_ai_reformat_present():
    """Une case « Reformatage par IA locale (Qwen…) » est présente."""
    assert "ai_reform_check" in SRC
    assert "Qwen" in SRC


# ── Dictée d'essai réelle (bonus) ─────────────────────────────────────────────

def test_worker_dictee_essai_defini():
    """Un QThread dédié à la dictée d'essai est défini."""
    classes = {n.name for n in ast.walk(TREE) if isinstance(n, ast.ClassDef)}
    assert "_TrialDictationWorker" in classes


def test_dictee_essai_hors_thread_gui():
    """La dictée d'essai transcrit via un QThread (jamais sur le thread GUI)."""
    # Le worker crée un Transcriber et émet ses résultats par signaux.
    assert "Transcriber(" in SRC
    assert "_start_trial_dictation" in SRC
    # Le worker hérite bien de QThread.
    worker = next(
        (n for n in ast.walk(TREE)
         if isinstance(n, ast.ClassDef) and n.name == "_TrialDictationWorker"),
        None,
    )
    assert worker is not None
    bases = {b.id for b in worker.bases if isinstance(b, ast.Name)}
    assert "QThread" in bases


def test_message_essai_indisponible():
    """Un message de repli non bloquant est prévu si l'essai échoue."""
    assert "Essai indisponible" in SRC


# ── _config_draft : nouvelles clés + défauts ──────────────────────────────────

def _config_draft_literals():
    """Extrait les littéraux du dict self._config_draft par analyse AST."""
    draft = None
    for node in ast.walk(TREE):
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
    return literals


def test_config_draft_contient_nouvelles_cles():
    """_config_draft porte toutes les clés attendues, anciennes + nouvelles."""
    lits = _config_draft_literals()
    for key in ("language", "model", "reformatting", "first_run",
                "bar_x", "bar_y", "beam_size", "input_device",
                "compute_backend", "ai_reformat"):
        assert key in lits, f"clé _config_draft manquante : {key!r}"


def test_config_draft_defauts():
    """Les défauts du brouillon sont cohérents (Phase 0 + Phase 3b)."""
    lits = _config_draft_literals()
    assert lits.get("beam_size") == 1
    assert lits.get("input_device") is None
    assert lits.get("compute_backend") == "auto"
    assert lits.get("ai_reformat") is False


def test_snapshot_input_device_dans_on_next():
    """_on_next snapshotte input_device (page Mic test)."""
    assert '_config_draft["input_device"]' in SRC


def test_snapshot_ai_reformat_dans_on_next():
    """_on_next snapshotte ai_reformat (page Config)."""
    assert '_config_draft["ai_reformat"]' in SRC


# ── Préservation de l'existant ────────────────────────────────────────────────

def test_progressdots_total_inchange():
    """Le total des ProgressDots reste 7 (aucune page ajoutée/retirée)."""
    assert "ProgressDots(total=7)" in SRC


def test_export_langs_preserve():
    """L'import/export de LANGS depuis settings_window est conservé."""
    assert "from ui.settings_window import LANGS" in SRC
