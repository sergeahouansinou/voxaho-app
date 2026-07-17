"""
Tests d'intégration PHASE 0 « Vitesse foudroyante » — volet intégration.

IMPORTANT : aucun QApplication ni QWidget n'est instancié ici. On vérifie
uniquement :
  1. les nouveaux défauts de config exposés par main.py (import sûr),
  2. la validité syntaxique de ui/floating_bar.py (ast.parse),
  3. la présence du préchargement modèle en thread daemon dans le source.

Ces vérifications restent exécutables headless (pas de backend audio/Whisper,
pas de serveur X / Qt).
"""

import ast
import sys
from pathlib import Path

# Racine du projet importable quel que soit le mode d'invocation de pytest.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FLOATING_BAR_PATH = ROOT / "ui" / "floating_bar.py"


# ── 1. Défauts de config (main.py) ────────────────────────────────────────────

class TestDefaultConfig:
    """main.py doit exposer les nouvelles clés du contrat moteur PHASE 0."""

    def test_import_main_sur(self):
        """`import main` ne doit déclencher aucun effet de bord (pas de QApp)."""
        import main  # noqa: F401 — l'import ne doit pas lever
        assert hasattr(main, "DEFAULT_CONFIG")

    def test_cle_input_device(self):
        import main
        assert "input_device" in main.DEFAULT_CONFIG
        assert main.DEFAULT_CONFIG["input_device"] is None

    def test_cle_beam_size(self):
        import main
        assert "beam_size" in main.DEFAULT_CONFIG
        assert main.DEFAULT_CONFIG["beam_size"] == 1

    def test_cle_compute_backend(self):
        import main
        assert "compute_backend" in main.DEFAULT_CONFIG
        assert main.DEFAULT_CONFIG["compute_backend"] == "auto"

    def test_cles_existantes_preservees(self):
        """Les clés historiques ne doivent pas avoir disparu (non-régression)."""
        import main
        for key in ("language", "model", "reformatting", "first_run",
                    "bar_x", "bar_y", "win_key"):
            assert key in main.DEFAULT_CONFIG


# ── 2. Validité syntaxique de floating_bar.py ─────────────────────────────────

class TestFloatingBarSyntax:

    def test_ast_parse_ok(self):
        """ui/floating_bar.py doit rester syntaxiquement valide (compile AST)."""
        source = FLOATING_BAR_PATH.read_text(encoding="utf-8")
        ast.parse(source)  # lève SyntaxError si invalide


# ── 3. Préchargement modèle en thread daemon ──────────────────────────────────

class TestPreloadPresent:
    """Le gain majeur du ressenti : le modèle est préchargé au démarrage dans
    un thread daemon. On vérifie sa présence dans le source sans instancier Qt.
    """

    def test_source_mentionne_preload_daemon(self):
        source = FLOATING_BAR_PATH.read_text(encoding="utf-8")
        assert "preload" in source
        assert "daemon=True" in source
        assert "voxaho-preload" in source

    def test_thread_preload_dans_le_code(self):
        """Contrôle AST léger : un threading.Thread est bien construit avec
        daemon=True et le nom "voxaho-preload" (le thread de préchargement).
        """
        tree = ast.parse(FLOATING_BAR_PATH.read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Cible threading.Thread(...) ou Thread(...)
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "Thread":
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            has_daemon = (
                "daemon" in kwargs
                and isinstance(kwargs["daemon"], ast.Constant)
                and kwargs["daemon"].value is True
            )
            has_preload_name = (
                "name" in kwargs
                and isinstance(kwargs["name"], ast.Constant)
                and kwargs["name"].value == "voxaho-preload"
            )
            if has_daemon and has_preload_name:
                found = True
                break
        assert found, "Aucun threading.Thread(daemon=True, name='voxaho-preload') trouvé"
