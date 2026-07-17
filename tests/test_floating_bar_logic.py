"""
Tests des fonctions pures de la barre flottante et du hotkey.

IMPORTANT : aucun QWidget ni QApplication n'est instancié ici — on ne teste
que la logique pure (clamp_to_screen, mapping vkCodes), exécutable headless.
"""

import sys
from pathlib import Path

# Garantit que la racine du projet est importable, quel que soit le mode
# d'invocation de pytest (python -m pytest, pytest direct, IDE…)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.hotkey import WIN_VK_CODES
from ui.floating_bar import CLAMP_MARGIN, clamp_to_screen


# Écran de référence : 1920×1080 à l'origine
SCREEN = (0, 0, 1920, 1080)
BAR_W, BAR_H = 340, 44


# ── clamp_to_screen ───────────────────────────────────────────────────────────

class TestClampToScreen:

    def test_coords_valides_inchangees(self):
        """Une position entièrement visible ne doit pas être modifiée."""
        assert clamp_to_screen(500, 300, BAR_W, BAR_H, SCREEN) == (500, 300)

    def test_x_zero_conserve(self):
        """x=0 (bord gauche) est une position légitime — ne pas la déplacer.

        C'était le bug d'origine : `config.get("bar_x") or défaut` traitait
        0 comme absent. La fonction de clamp ne doit pas réintroduire ce biais.
        """
        assert clamp_to_screen(0, 300, BAR_W, BAR_H, SCREEN) == (0, 300)

    def test_y_zero_conserve(self):
        """Symétrique : y=0 (bord haut) est aussi légitime."""
        assert clamp_to_screen(500, 0, BAR_W, BAR_H, SCREEN) == (500, 0)

    def test_hors_ecran_droite(self):
        """Barre au-delà du bord droit → ramenée dedans avec marge."""
        x, y = clamp_to_screen(5000, 300, BAR_W, BAR_H, SCREEN)
        assert x == 1920 - BAR_W - CLAMP_MARGIN
        assert y == 300
        # Entièrement visible
        assert 0 <= x and x + BAR_W <= 1920

    def test_hors_ecran_gauche(self):
        """Barre au-delà du bord gauche → ramenée dedans avec marge."""
        x, y = clamp_to_screen(-800, 300, BAR_W, BAR_H, SCREEN)
        assert x == CLAMP_MARGIN
        assert y == 300

    def test_hors_ecran_bas(self):
        """Barre sous le bord bas → ramenée dedans avec marge."""
        x, y = clamp_to_screen(500, 4000, BAR_W, BAR_H, SCREEN)
        assert x == 500
        assert y == 1080 - BAR_H - CLAMP_MARGIN
        assert 0 <= y and y + BAR_H <= 1080

    def test_hors_ecran_haut(self):
        """Barre au-dessus du bord haut → ramenée dedans avec marge."""
        x, y = clamp_to_screen(500, -200, BAR_W, BAR_H, SCREEN)
        assert x == 500
        assert y == CLAMP_MARGIN

    def test_hors_ecran_deux_axes(self):
        """Les deux axes sont corrigés indépendamment."""
        x, y = clamp_to_screen(-999, 9999, BAR_W, BAR_H, SCREEN)
        assert (x, y) == (CLAMP_MARGIN, 1080 - BAR_H - CLAMP_MARGIN)

    def test_barre_plus_grande_que_ecran(self):
        """Barre plus large ET plus haute que l'écran → coin haut-gauche + marge."""
        x, y = clamp_to_screen(100, 100, 3000, 2000, SCREEN)
        assert (x, y) == (CLAMP_MARGIN, CLAMP_MARGIN)

    def test_barre_plus_large_seulement(self):
        """Seule la largeur dépasse → x au bord + marge, y valide inchangé."""
        x, y = clamp_to_screen(100, 300, 3000, BAR_H, SCREEN)
        assert x == CLAMP_MARGIN
        assert y == 300

    def test_ecran_decale(self):
        """Écran secondaire non situé à l'origine (ex. moniteur à droite).

        Cas typique du bug M5 : coords sauvegardées sur l'ancien écran
        (origine 0,0) alors que l'écran courant démarre à x=1920.
        """
        screen2 = (1920, 0, 1440, 900)
        # Position héritée de l'ancien écran → hors zone visible à gauche
        x, y = clamp_to_screen(500, 300, BAR_W, BAR_H, screen2)
        assert x == 1920 + CLAMP_MARGIN
        assert y == 300
        # Position déjà valide sur l'écran décalé → inchangée
        assert clamp_to_screen(2500, 400, BAR_W, BAR_H, screen2) == (2500, 400)

    def test_retour_entiers(self):
        """La fonction retourne toujours des int (attendu par Qt move/resize)."""
        x, y = clamp_to_screen(500.7, 300.2, BAR_W, BAR_H, SCREEN)
        assert isinstance(x, int) and isinstance(y, int)


# ── Mapping vkCodes hotkey (Windows) ─────────────────────────────────────────

class TestWinVkCodes:

    def test_valeurs_vkcodes(self):
        """Les 5 vkCodes Windows utilisés par win32_event_filter (M2/M9)."""
        assert WIN_VK_CODES["ctrl_r"]    == 0xA3  # VK_RCONTROL
        assert WIN_VK_CODES["ctrl_l"]    == 0xA2  # VK_LCONTROL
        assert WIN_VK_CODES["alt_r"]     == 0xA5  # VK_RMENU
        assert WIN_VK_CODES["shift_r"]   == 0xA1  # VK_RSHIFT
        assert WIN_VK_CODES["caps_lock"] == 0x14  # VK_CAPITAL

    def test_couvre_toutes_les_options_ui(self):
        """Chaque option proposée dans le wizard/préférences a un vkCode."""
        assert set(WIN_VK_CODES) == {
            "ctrl_r", "ctrl_l", "alt_r", "shift_r", "caps_lock"
        }
