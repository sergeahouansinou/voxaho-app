"""
Tests PHASE 3 « Au-delà de Wispr » — câblage de la traduction à la volée.

IMPORTANT : aucun QWidget ni QApplication n'est instancié ici. Instancier
SettingsWindow / FloatingBar exige QApplication (+ permissions système) ; on se
limite donc à :
  1. la vérification du défaut de config exposé par main.py (`import main` est
     sûr — aucun effet de bord, pas de QApp) ;
  2. de l'ANALYSE STATIQUE (ast + lecture du source) pour vérifier le câblage
     bout-en-bout de la clé `translate_to` :
       - ui/settings_window.py : section « Traduction », combo, lecture dans
         _load_values et écriture dans _collect ;
       - ui/floating_bar.py : `_build_transcriber` passe translate_to,
         `apply_config` l'applique à chaud, helper `_apply_translate_to` défensif ;
  3. la validité syntaxique des deux modules modifiés (ast.parse ne lève pas).

Ces vérifications restent exécutables headless (pas de Qt, pas de Whisper/LLM).

Lancer : ./venv/bin/python -m pytest tests/test_translation_wiring.py -v
"""

import ast
import sys
from pathlib import Path

# Racine du projet importable quel que soit le mode d'invocation de pytest.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SETTINGS_PATH = ROOT / "ui" / "settings_window.py"
FLOATING_BAR_PATH = ROOT / "ui" / "floating_bar.py"

_SETTINGS_SRC = SETTINGS_PATH.read_text(encoding="utf-8")
_BAR_SRC = FLOATING_BAR_PATH.read_text(encoding="utf-8")
_SETTINGS_TREE = ast.parse(_SETTINGS_SRC)
_BAR_TREE = ast.parse(_BAR_SRC)


# ── Outils d'analyse AST (headless, sans Qt) ──────────────────────────────────

def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _function_source(tree: ast.Module, src: str, name: str) -> str:
    node = _find_function(tree, name)
    assert node is not None, f"fonction/méthode {name!r} introuvable"
    seg = ast.get_source_segment(src, node)
    assert seg is not None
    return seg


# ── 1. Défaut de config (main.py) ─────────────────────────────────────────────

class TestDefaultConfig:
    """main.py doit exposer la nouvelle clé `translate_to` (défaut None)."""

    def test_import_main_sur(self):
        """`import main` ne doit déclencher aucun effet de bord (pas de QApp)."""
        import main  # noqa: F401 — l'import ne doit pas lever
        assert hasattr(main, "DEFAULT_CONFIG")

    def test_cle_translate_to_presente(self):
        import main
        assert "translate_to" in main.DEFAULT_CONFIG

    def test_translate_to_defaut_none(self):
        import main
        assert main.DEFAULT_CONFIG["translate_to"] is None

    def test_cles_existantes_preservees(self):
        """Non-régression : les clés historiques ne doivent pas disparaître."""
        import main
        for key in ("language", "model", "reformatting", "first_run",
                    "bar_x", "bar_y", "win_key", "input_device",
                    "beam_size", "compute_backend", "ai_reformat"):
            assert key in main.DEFAULT_CONFIG


# ── 2. Validité syntaxique des modules modifiés ───────────────────────────────

class TestSyntaxe:

    def test_settings_window_parse(self):
        assert isinstance(ast.parse(_SETTINGS_SRC), ast.Module)

    def test_floating_bar_parse(self):
        assert isinstance(ast.parse(_BAR_SRC), ast.Module)


# ── 3. UI settings_window : section « Traduction » + combo ────────────────────

class TestSettingsUI:

    def test_section_traduction_presente(self):
        """La section « Traduction » et le libellé « Traduire… » sont en source."""
        assert "Traduction" in _SETTINGS_SRC
        assert "Traduire" in _SETTINGS_SRC

    def test_combo_translate_construit(self):
        """Le combo self.cb_translate est bien construit (QComboBox)."""
        assert "self.cb_translate" in _SETTINGS_SRC
        assert "QComboBox" in _SETTINGS_SRC

    def test_entree_desactive_valeur_none(self):
        """1ʳᵉ entrée « Désactivé » avec currentData None."""
        build = _function_source(_SETTINGS_TREE, _SETTINGS_SRC, "_build_general_tab")
        assert "Désactivé" in build
        # Entrée désactivée = valeur None dans le combo.
        assert 'addItem("Désactivé (garder la langue dictée)", None)' in build

    def test_hint_contrat_present(self):
        """Le hint du contrat (Qwen) est présent."""
        assert "Nécessite le modèle IA (Qwen)." in _SETTINGS_SRC

    def test_import_translator_defensif(self):
        """core.translator est importé défensivement : absent → False, pas
        d'exception. Sur cette installation le module peut être absent."""
        from ui.settings_window import _translator_available
        result = _translator_available()
        assert result in (True, False)


# ── 4. Lecture / écriture de la clé `translate_to` ────────────────────────────

class TestSettingsWiring:

    def test_load_values_lit_translate_to(self):
        """_load_values lit cfg['translate_to'] (défaut None) dans le combo."""
        body = _function_source(_SETTINGS_TREE, _SETTINGS_SRC, "_load_values")
        assert 'cfg.get("translate_to", None)' in body
        assert "self.cb_translate.setCurrentIndex" in body

    def test_collect_ecrit_translate_to(self):
        """_collect écrit cfg['translate_to'] = self.cb_translate.currentData()."""
        body = _function_source(_SETTINGS_TREE, _SETTINGS_SRC, "_collect")
        assert 'cfg["translate_to"]' in body
        assert "self.cb_translate.currentData()" in body

    def test_translate_to_distinct_de_language(self):
        """La clé cible `translate_to` n'écrase pas la langue de dictée."""
        body = _function_source(_SETTINGS_TREE, _SETTINGS_SRC, "_collect")
        assert 'cfg["language"]' in body
        assert 'cfg["translate_to"]' in body


# ── 5. Câblage translate_to dans floating_bar ─────────────────────────────────

class TestBuildTranscriber:

    def test_translate_to_lu_depuis_config(self):
        """_build_transcriber lit translate_to depuis self.config."""
        src = _function_source(_BAR_TREE, _BAR_SRC, "_build_transcriber")
        assert 'self.config.get("translate_to"' in src

    def test_translate_to_passe_au_constructeur(self):
        """translate_to est passé au constructeur Transcriber (défensif)."""
        src = _function_source(_BAR_TREE, _BAR_SRC, "_build_transcriber")
        assert ("translate_to = translate_to" in src
                or "translate_to=translate_to" in src)

    def test_kwargs_recents_preserves(self):
        """Non-régression : beam_size/backend/ai_reformat restent passés."""
        src = _function_source(_BAR_TREE, _BAR_SRC, "_build_transcriber")
        for kw in ("beam_size", "backend", "ai_reformat"):
            assert kw in src


class TestApplyConfig:

    def test_apply_config_applique_translate_to(self):
        """apply_config applique translate_to à chaud (hot-swap sans recréation)."""
        src = _function_source(_BAR_TREE, _BAR_SRC, "apply_config")
        assert "translate_to" in src

    def test_helper_apply_translate_to_existe(self):
        """Un helper _apply_translate_to (style _apply_ai_reformat) est présent."""
        assert _find_function(_BAR_TREE, "_apply_translate_to") is not None

    def test_apply_translate_to_defensif(self):
        """Le helper tente update_settings puis retombe sur setattr (défensif)."""
        src = _function_source(_BAR_TREE, _BAR_SRC, "_apply_translate_to")
        assert "update_settings" in src
        assert "translate_to" in src
        # Repli setattr direct sur l'attribut du moteur.
        assert "self._transcriber.translate_to = translate_to" in src
