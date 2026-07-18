"""
Tests de core.appcontext (Phase 3b « Profils par application »).

Deux volets :
  1. match_profile — fonction PURE : correspondance « motif sous-chaîne du nom
     d'app » insensible à la casse, cas limites (None, liste vide, motif vide,
     premier match prioritaire). Aucune dépendance plateforme, aucun Qt.
  2. active_app — ne doit JAMAIS lever. Sur cette machine elle peut renvoyer un
     nom d'app ou None ; on vérifie surtout l'absence d'exception et le bon
     aiguillage plateforme (monkeypatch des helpers, pas de sys.modules).

Lancer : ./venv/bin/python -m pytest tests/test_appcontext.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import appcontext


# ── match_profile (pur) ──────────────────────────────────────────────────────

class TestMatchProfile:

    def test_pattern_sous_chaine_du_nom(self):
        """Motif « Code » matche « Visual Studio Code »."""
        profs = [{"app_pattern": "Code"}]
        assert appcontext.match_profile("Visual Studio Code", profs) is profs[0]

    def test_insensible_a_la_casse(self):
        """La correspondance ignore la casse dans les deux sens."""
        profs = [{"app_pattern": "CHROME"}]
        assert appcontext.match_profile("Google Chrome", profs) is profs[0]
        profs2 = [{"app_pattern": "mail"}]
        assert appcontext.match_profile("MAIL", profs2) is profs2[0]

    def test_app_name_none_retourne_none(self):
        assert appcontext.match_profile(None, [{"app_pattern": "x"}]) is None

    def test_app_name_vide_retourne_none(self):
        assert appcontext.match_profile("", [{"app_pattern": "x"}]) is None

    def test_liste_vide_retourne_none(self):
        assert appcontext.match_profile("Mail", []) is None

    def test_aucune_correspondance(self):
        assert appcontext.match_profile("Safari", [{"app_pattern": "Code"}]) is None

    def test_premier_match_prioritaire(self):
        """Le PREMIER profil correspondant dans l'ordre de la liste gagne."""
        p1 = {"app_pattern": "o", "name": "premier"}   # « o » ⊂ « Code »
        p2 = {"app_pattern": "Code", "name": "second"}
        assert appcontext.match_profile("Code", [p1, p2]) is p1

    def test_motif_vide_ignore(self):
        """Un motif vide (après trim) ne matche jamais et n'avale rien."""
        p_vide = {"app_pattern": "   "}
        p_ok = {"app_pattern": "Code"}
        assert appcontext.match_profile("VS Code", [p_vide, p_ok]) is p_ok

    def test_motif_absent_ignore_defensivement(self):
        """Un profil sans clé app_pattern est ignoré sans lever."""
        result = appcontext.match_profile(
            "Code", [{"name": "x"}, {"app_pattern": "Code"}]
        )
        assert result is not None and result["app_pattern"] == "Code"

    def test_sens_inverse_non_retenu(self):
        """Le nom d'app sous-chaîne du motif n'est PAS une correspondance."""
        # app « Code », motif « Visual Studio Code » : motif PLUS long → pas de match.
        assert appcontext.match_profile("Code", [{"app_pattern": "Visual Studio Code"}]) is None


# ── active_app (défensif, jamais d'exception) ────────────────────────────────

class TestActiveApp:

    def test_ne_leve_jamais(self):
        """L'appel réel ne doit pas lever ; renvoie None ou un nom (str)."""
        result = appcontext.active_app()
        assert result is None or isinstance(result, str)

    def test_plateforme_inconnue_retourne_none(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert appcontext.active_app() is None

    def test_aiguillage_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(appcontext, "_active_app_macos", lambda: "TestApp")
        assert appcontext.active_app() == "TestApp"

    def test_aiguillage_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(appcontext, "_active_app_windows", lambda: "Notepad")
        assert appcontext.active_app() == "Notepad"

    def test_helper_qui_leve_est_neutralise(self, monkeypatch):
        """Même si l'accès plateforme lève, active_app renvoie None (garde-fou)."""
        def _boom():
            raise RuntimeError("API plateforme cassée")

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(appcontext, "_active_app_macos", _boom)
        assert appcontext.active_app() is None

    def test_helper_macos_defensif_sans_appkit(self, monkeypatch):
        """_active_app_macos retombe sur None si AppKit est introuvable."""
        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "AppKit" or name.startswith("AppKit."):
                raise ImportError("AppKit indisponible")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        assert appcontext._active_app_macos() is None
