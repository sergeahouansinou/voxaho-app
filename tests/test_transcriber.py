"""
Tests du reformatage (_reformat) et du filtre d'hallucinations (_is_hallucination).

Aucun modèle Whisper n'est chargé : le chargement est lazy et transcribe()
n'est jamais appelé ici. On teste uniquement les fonctions pures de
post-traitement du texte.
"""

import sys
from pathlib import Path

# Rendre le package `core` importable quel que soit le mode d'invocation de pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.transcriber import (
    FILLERS_BY_LANG,
    Transcriber,
    _is_hallucination,
    _normalize_for_blocklist,
)


@pytest.fixture
def transcriber():
    # reformatting=True, mais le modèle n'est jamais chargé (lazy load)
    return Transcriber(model="small", language="auto", reformatting=True)


# ── C1 : suppression des fillers sans détruire de vrais mots ─────────────────

class TestFillers:
    def test_like_anglais_conserve(self, transcriber):
        # "like" est un verbe légitime, il ne doit jamais être supprimé
        assert transcriber._reformat("I like pizza", "en") == "I like pizza."

    def test_euh_supprime_en_francais(self, transcriber):
        assert transcriber._reformat("euh bonjour", "fr") == "Bonjour."

    def test_um_supprime_en_anglais(self, transcriber):
        assert transcriber._reformat("um hello", "en") == "Hello."

    def test_quoi_conserve_en_francais(self, transcriber):
        assert (
            transcriber._reformat("il ne sait pas quoi faire", "fr")
            == "Il ne sait pas quoi faire."
        )

    def test_prenom_ben_conserve(self, transcriber):
        assert transcriber._reformat("ben est arrivé", "fr") == "Ben est arrivé."

    def test_filler_au_milieu_de_phrase(self, transcriber):
        assert (
            transcriber._reformat("bonjour euh comment ça va", "fr")
            == "Bonjour comment ça va."
        )

    def test_um_allemand_conserve(self, transcriber):
        # "um" est une préposition en allemand : "um 5 Uhr" = "à 5 heures"
        assert (
            transcriber._reformat("wir treffen uns um 5 Uhr", "de")
            == "Wir treffen uns um 5 Uhr."
        )

    def test_aehm_supprime_en_allemand(self, transcriber):
        assert transcriber._reformat("ähm ja", "de") == "Ja."

    def test_ehm_supprime_en_italien(self, transcriber):
        assert transcriber._reformat("uhm va bene", "it") == "Va bene."

    def test_este_esto_conserves_en_espagnol(self, transcriber):
        # "este"/"esto" sont de vrais démonstratifs espagnols
        assert (
            transcriber._reformat("esto es muy importante", "es")
            == "Esto es muy importante."
        )

    def test_langue_inconnue_aucune_suppression(self, transcriber):
        # Langue non listée → aucun filler supprimé, juste ponctuation/majuscule
        assert transcriber._reformat("euh um bonjour", "nl") == "Euh um bonjour."

    def test_langue_none_aucune_suppression(self, transcriber):
        assert transcriber._reformat("euh bonjour", None) == "Euh bonjour."

    def test_aucun_filler_ambigu_dans_les_listes(self):
        # Garde-fou : aucun vrai mot ne doit figurer dans FILLERS_BY_LANG
        mots_interdits = {
            "like", "quoi", "ben", "bah", "voilà", "enfin", "hein",
            "you know", "i mean", "kind of", "sort of", "basically",
            "literally", "so", "well", "eh", "este", "esto", "ja", "also",
        }
        for lang, fillers in FILLERS_BY_LANG.items():
            intersection = mots_interdits & {f.lower() for f in fillers}
            assert not intersection, f"filler ambigu en '{lang}': {intersection}"


# ── M8 : recapitalisation Unicode après ponctuation ──────────────────────────

class TestRecapitalisation:
    def test_majuscule_unicode_allemand(self, transcriber):
        assert transcriber._reformat("hallo. ärger", "de") == "Hallo. Ärger."

    def test_majuscule_unicode_espagnol(self, transcriber):
        assert transcriber._reformat("hola. ñoño llegó", "es") == "Hola. Ñoño llegó."

    def test_majuscule_apres_exclamation(self, transcriber):
        assert transcriber._reformat("super! à demain", "fr") == "Super! À demain."


# ── Préservation des URLs ─────────────────────────────────────────────────────

class TestUrls:
    def test_url_preservee_avec_filler(self, transcriber):
        out = transcriber._reformat("va sur https://voxaho.com euh maintenant", "fr")
        assert "https://voxaho.com" in out
        assert "euh" not in out
        assert out == "Va sur https://voxaho.com maintenant."


# ── M3 : filtre des hallucinations Whisper ────────────────────────────────────

class TestHallucinations:
    def test_credits_amara_filtres(self):
        assert _is_hallucination("Sous-titres réalisés par la communauté d'Amara.org")

    def test_amara_cite_dans_une_dictee_conserve(self):
        # Le motif ne couvre qu'une petite partie du segment → vraie dictée
        assert not _is_hallucination(
            "va sur amara.org pour voir les sous-titres de la conférence"
        )

    def test_merci_d_avoir_regarde_filtre(self):
        assert _is_hallucination("Merci d'avoir regardé !")

    def test_merci_d_avoir_regarde_dans_une_phrase_conserve(self):
        assert not _is_hallucination("merci d'avoir regardé mon dossier en détail")

    def test_thank_you_for_watching_filtre(self):
        assert _is_hallucination("Thank you for watching!")

    def test_abonnez_vous_filtre(self):
        assert _is_hallucination("Abonnez-vous !")

    def test_variantes_multilingues_filtrees(self):
        assert _is_hallucination("Subtitles by the Amara.org community")
        assert _is_hallucination("Subtítulos realizados por la comunidad de Amara.org")
        assert _is_hallucination("Untertitelung aufgrund der Amara.org-Community")
        assert _is_hallucination("Sottotitoli creati dalla comunità Amara.org")

    def test_motif_couvrant_au_moins_80_pourcent(self):
        # Le motif + un mot résiduel : couverture ≥ 80 % → filtré
        assert _is_hallucination(
            "Sous-titres réalisés par la communauté d'Amara.org. Merci."
        )

    def test_phrase_normale_conservee(self):
        assert not _is_hallucination("bonjour, je voudrais dicter une note")

    def test_segment_vide_conserve(self):
        assert not _is_hallucination("")
        assert not _is_hallucination("   ")

    def test_normalisation(self):
        # La normalisation neutralise casse, ponctuation et apostrophes typographiques
        assert (
            _normalize_for_blocklist("Sous-titres réalisés par la communauté d’Amara.org")
            == _normalize_for_blocklist("sous-titres réalisés par la communauté d'amara.org")
        )
