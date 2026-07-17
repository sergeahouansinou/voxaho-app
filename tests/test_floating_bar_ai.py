"""
Tests PHASE 2B « Reformatage IA local » — câblage de la clé `ai_reformat`.

IMPORTANT : aucun QWidget ni QApplication n'est instancié ici. Instancier
FloatingBar exige QApplication + permissions système ; on se limite donc à :
  1. la vérification du défaut de config exposé par main.py (`import main` est
     sûr — aucun effet de bord, pas de QApp) ;
  2. de l'ANALYSE STATIQUE (ast + lecture du source) pour vérifier le câblage
     bout-en-bout dans ui/floating_bar.py :
       - `_build_transcriber` passe `ai_reformat` au constructeur Transcriber,
       - `apply_config` applique `ai_reformat` à chaud,
       - `closeEvent` libère le LLM via `llm.unload()` ;
  3. la validité syntaxique de ui/floating_bar.py (ast.parse ne lève pas).

Ces vérifications restent exécutables headless (pas de backend audio/Whisper,
pas de LLM, pas de serveur X / Qt).
"""

import ast
import sys
from pathlib import Path

# Racine du projet importable quel que soit le mode d'invocation de pytest.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FLOATING_BAR_PATH = ROOT / "ui" / "floating_bar.py"
_SOURCE = FLOATING_BAR_PATH.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _class_node(name: str) -> ast.ClassDef:
    for node in ast.walk(_TREE):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"Classe {name!r} introuvable")


def _method_source(class_name: str, method_name: str) -> str:
    """Retourne le source EXACT d'une méthode (isole les assertions à son corps)."""
    cls = _class_node(class_name)
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method_name:
            seg = ast.get_source_segment(_SOURCE, n)
            assert seg is not None, f"Source de {method_name} introuvable"
            return seg
    raise AssertionError(f"Méthode {class_name}.{method_name!r} introuvable")


# ── 1. Défaut de config (main.py) ─────────────────────────────────────────────

class TestDefaultConfig:
    """main.py doit exposer la nouvelle clé `ai_reformat` (défaut False)."""

    def test_import_main_sur(self):
        """`import main` ne doit déclencher aucun effet de bord (pas de QApp)."""
        import main  # noqa: F401 — l'import ne doit pas lever
        assert hasattr(main, "DEFAULT_CONFIG")

    def test_cle_ai_reformat_presente(self):
        import main
        assert "ai_reformat" in main.DEFAULT_CONFIG

    def test_ai_reformat_defaut_false(self):
        import main
        assert main.DEFAULT_CONFIG["ai_reformat"] is False

    def test_cles_existantes_preservees(self):
        """Non-régression : les clés historiques ne doivent pas disparaître."""
        import main
        for key in ("language", "model", "reformatting", "first_run",
                    "bar_x", "bar_y", "win_key", "input_device",
                    "beam_size", "compute_backend"):
            assert key in main.DEFAULT_CONFIG


# ── 2. Validité syntaxique de floating_bar.py ─────────────────────────────────

class TestSyntaxe:

    def test_source_parse_sans_erreur(self):
        """ui/floating_bar.py doit rester syntaxiquement valide (compile AST)."""
        assert isinstance(_TREE, ast.Module)


# ── 3. Câblage ai_reformat dans _build_transcriber ────────────────────────────

class TestBuildTranscriber:

    def test_ai_reformat_lu_depuis_config(self):
        """_build_transcriber lit ai_reformat depuis self.config."""
        src = _method_source("FloatingBar", "_build_transcriber")
        assert 'self.config.get("ai_reformat"' in src

    def test_ai_reformat_passe_au_constructeur(self):
        """ai_reformat est passé au constructeur Transcriber (au même niveau
        défensif que beam_size/backend)."""
        src = _method_source("FloatingBar", "_build_transcriber")
        assert "ai_reformat" in src
        # Passé en kwarg du Transcriber (au moins une occurrence en argument)
        assert "ai_reformat  = ai_reformat" in src or "ai_reformat=ai_reformat" in src


# ── 4. Application à chaud dans apply_config ──────────────────────────────────

class TestApplyConfig:

    def test_apply_config_applique_ai_reformat(self):
        """apply_config applique ai_reformat à chaud (hot-swap sans recréation)."""
        src = _method_source("FloatingBar", "apply_config")
        assert "ai_reformat" in src

    def test_helper_apply_ai_reformat_existe(self):
        """Un helper _apply_ai_reformat (style _apply_beam_size) est présent."""
        cls = _class_node("FloatingBar")
        names = {
            n.name for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "_apply_ai_reformat" in names

    def test_apply_ai_reformat_defensif(self):
        """Le helper tente update_settings puis retombe sur setattr (défensif)."""
        src = _method_source("FloatingBar", "_apply_ai_reformat")
        assert "update_settings" in src
        assert "ai_reformat" in src


# ── 5. Libération du LLM dans closeEvent ──────────────────────────────────────

class TestCloseEventLLM:

    def test_close_event_appelle_llm_unload(self):
        """closeEvent libère l'instance LLM via llm.unload() (défensif)."""
        src = _method_source("FloatingBar", "closeEvent")
        assert "llm.unload()" in src
        assert "from core import llm" in src

    def test_llm_unload_dans_try_except(self):
        """L'appel llm.unload() est protégé par un try/except (ne bloque pas
        la fermeture)."""
        cls = _class_node("FloatingBar")
        close_node = next(
            n for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "closeEvent"
        )
        found = False
        for node in ast.walk(close_node):
            if not isinstance(node, ast.Try):
                continue
            # Un appel à llm.unload() est-il présent dans ce bloc try ?
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "unload"):
                    found = True
                    break
            if found:
                break
        assert found, "llm.unload() n'est pas protégé par un try/except dans closeEvent"

    def test_unload_model_transcriber_preserve(self):
        """Non-régression : la libération du modèle Whisper reste présente."""
        src = _method_source("FloatingBar", "closeEvent")
        assert "unload_model()" in src
