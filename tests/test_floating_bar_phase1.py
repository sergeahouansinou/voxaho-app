"""
Tests Phase 1 « Le Workspace » — points d'intégration de la barre flottante.

IMPORTANT : aucun QWidget ni QApplication n'est instancié ici. Instancier
FloatingBar exige QApplication + permissions système ; on se limite donc à :
  1. de l'ANALYSE STATIQUE (ast + lecture du source) pour vérifier la présence
     des points d'intégration (enregistrement historique, ouverture Workspace,
     rafraîchissement live, lien cliquable) ;
  2. le test de la petite fonction PURE open_workspace_link_geom, importable
     sans QApplication.
"""

import ast
import sys
from pathlib import Path

# Racine du projet importable quel que soit le mode d'invocation de pytest
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ui.floating_bar import open_workspace_link_geom

FLOATING_BAR_PATH = ROOT / "ui" / "floating_bar.py"
_SOURCE = FLOATING_BAR_PATH.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _class_node(name: str) -> ast.ClassDef:
    for node in ast.walk(_TREE):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"Classe {name!r} introuvable")


def _method_names(class_name: str) -> set[str]:
    cls = _class_node(class_name)
    return {
        n.name
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


# ── Analyse statique : le module reste syntaxiquement valide ──────────────────

class TestSyntaxe:

    def test_source_parse_sans_erreur(self):
        """Le source doit rester du Python valide (ast.parse ne lève pas)."""
        assert isinstance(_TREE, ast.Module)


# ── §1/§4 Enregistrement dans l'historique + rafraîchissement ─────────────────

class TestEnregistrementHistorique:

    def test_methode_record_dictation_existe(self):
        assert "_record_dictation" in _method_names("FloatingBar")

    def test_appel_history_add_entry(self):
        """La dictée est bien poussée dans core.history via add_entry."""
        assert "history.add_entry(" in _SOURCE
        assert "from core import history" in _SOURCE

    def test_add_entry_defensif(self):
        """add_entry passe language / model / word_count (contrat couche données)."""
        assert "word_count" in _SOURCE
        assert "language" in _SOURCE
        assert "model" in _SOURCE

    def test_rafraichissement_workspace(self):
        """§4 : émission de dictation_added quand le Workspace est ouvert."""
        assert "dictation_added.emit()" in _SOURCE

    def test_record_appele_apres_injection(self):
        """_record_dictation doit être invoqué dans _on_transcription."""
        assert "self._record_dictation(text)" in _SOURCE


# ── §2 Ouverture du Workspace ─────────────────────────────────────────────────

class TestOuvertureWorkspace:

    def test_methode_open_workspace_existe(self):
        assert "_open_workspace" in _method_names("FloatingBar")

    def test_slot_fermeture_existe(self):
        assert "_on_workspace_closed" in _method_names("FloatingBar")

    def test_import_differe_workspace_window(self):
        """Import de WorkspaceWindow différé DANS la méthode (défensif)."""
        assert "from ui.workspace_window import WorkspaceWindow" in _SOURCE

    def test_reference_workspace_win_geree(self):
        """Une référence unique self._workspace_win est gardée puis remise à None."""
        assert "self._workspace_win" in _SOURCE
        assert "self._workspace_win = None" in _SOURCE

    def test_open_settings_fn_passe(self):
        """WorkspaceWindow reçoit open_settings_fn (contrat Workspace)."""
        assert "open_settings_fn=self._open_settings_window" in _SOURCE

    def test_reactivation_si_deja_ouverte(self):
        """Réutilise la fenêtre existante (raise_ + activateWindow)."""
        assert "activateWindow()" in _SOURCE


# ── §3 Déclencheur d'ouverture dans le panneau étendu ─────────────────────────

class TestLienOuvrirVoxaho:

    def test_chaine_ouvrir_voxaho_dans_dessin(self):
        """Le lien « Ouvrir Voxaho » est dessiné dans le panneau étendu."""
        assert "Ouvrir Voxaho" in _SOURCE

    def test_hit_test_utilise_la_geometrie_partagee(self):
        """Le hit-test appelle open_workspace_link_geom → cohérence dessin/clic."""
        assert "open_workspace_link_geom(bar_h)" in _SOURCE
        # ...et le clic déclenche bien l'ouverture
        assert "self._open_workspace()" in _SOURCE


# ── Fonction pure open_workspace_link_geom ────────────────────────────────────

# Rectangle cliquable de « ⚙ Personnaliser… » tel que dessiné : QRect(14, .., 170, 24)
PERSO_X, PERSO_W = 14, 170
BAR_H = 44  # hauteur barre pleine (show_frac ≈ 1 quand le panneau est ouvert)


class TestOpenWorkspaceLinkGeom:

    def test_retourne_quadruplet_int(self):
        geom = open_workspace_link_geom(BAR_H)
        assert len(geom) == 4
        assert all(isinstance(v, int) for v in geom)

    def test_calee_sur_la_ligne_personnaliser(self):
        """y du lien = même ligne (custom_y) que « Personnaliser… » (custom_y-16)."""
        custom_y = BAR_H + 16 + 4 * 26 + 10
        _x, y, _w, h = open_workspace_link_geom(BAR_H)
        assert y == custom_y - 16
        assert h == 24

    def test_pas_de_chevauchement_avec_personnaliser(self):
        """Le rect « Ouvrir Voxaho » ne recouvre pas celui de « Personnaliser… »."""
        x, _y, w, _h = open_workspace_link_geom(BAR_H)
        # QRect.contains : bord droit = x + w - 1
        perso_right = PERSO_X + PERSO_W - 1  # 183
        assert x > perso_right  # commence après le rect Personnaliser

    def test_tient_dans_la_largeur_barre(self):
        """Le lien ne déborde pas de la barre pleine (BAR_W = 340)."""
        from ui.floating_bar import BAR_W
        x, _y, w, _h = open_workspace_link_geom(BAR_H)
        assert x + w <= BAR_W

    def test_suit_la_hauteur_de_barre(self):
        """custom_y dépend de bar_h → un bar_h plus grand décale le lien vers le bas."""
        _x1, y1, _w1, _h1 = open_workspace_link_geom(BAR_H)
        _x2, y2, _w2, _h2 = open_workspace_link_geom(BAR_H + 10)
        assert y2 == y1 + 10
