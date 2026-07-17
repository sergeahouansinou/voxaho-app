"""
Tests de core.dictionary : CRUD des termes, amorce Whisper et correction
conservatrice des quasi-erreurs de reconnaissance.

Isolation totale : base SQLite neuve dans tmp_path via core.db.set_db_path().
Aucun modèle Whisper n'est chargé (on ne teste que la logique pure + la base).

Lancement : ./venv/bin/python -m pytest tests/test_dictionary.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import db, dictionary


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.set_db_path(str(tmp_path / "t.db"))
    yield
    db.set_db_path(db.DB_PATH)


# ── CRUD ───────────────────────────────────────────────────────────────────────

def test_add_et_list():
    tid = dictionary.add_term("Voxaho")
    assert tid > 0
    terms = dictionary.list_terms()
    assert len(terms) == 1
    assert terms[0]["term"] == "Voxaho"
    assert terms[0]["id"] == tid
    assert terms[0]["created_at"]


def test_add_trim():
    tid = dictionary.add_term("   KKiaPay   ")
    terms = dictionary.list_terms()
    assert terms[0]["term"] == "KKiaPay"
    assert terms[0]["id"] == tid


def test_add_vide_retourne_moins_un():
    assert dictionary.add_term("   ") == -1
    assert dictionary.list_terms() == []


def test_add_doublon_ignore_retourne_id_existant():
    id1 = dictionary.add_term("OHADA")
    id2 = dictionary.add_term("OHADA")
    assert id1 == id2
    assert len(dictionary.list_terms()) == 1


def test_list_ordre_alpha_insensible_casse():
    dictionary.add_term("zulu")
    dictionary.add_term("Alpha")
    dictionary.add_term("mike")
    termes = [t["term"] for t in dictionary.list_terms()]
    assert termes == ["Alpha", "mike", "zulu"]


def test_remove_term():
    id1 = dictionary.add_term("Voxaho")
    id2 = dictionary.add_term("OHADA")
    dictionary.remove_term(id1)
    restants = [t["term"] for t in dictionary.list_terms()]
    assert restants == ["OHADA"]
    # Suppression d'un id inexistant : ne lève pas, sans effet.
    dictionary.remove_term(99999)
    assert len(dictionary.list_terms()) == 1
    assert id2 > 0


# ── whisper_prompt ───────────────────────────────────────────────────────────

def test_whisper_prompt_vide():
    assert dictionary.whisper_prompt() == ""


def test_whisper_prompt_contenu():
    dictionary.add_term("Voxaho")
    dictionary.add_term("KKiaPay")
    dictionary.add_term("OHADA")
    prompt = dictionary.whisper_prompt()
    assert prompt.startswith("Termes : ")
    assert prompt.endswith(".")
    for terme in ("Voxaho", "KKiaPay", "OHADA"):
        assert terme in prompt


def test_whisper_prompt_tronque():
    for i in range(20):
        dictionary.add_term(f"TermeAssezLong{i:02d}")
    prompt = dictionary.whisper_prompt(max_chars=40)
    assert len(prompt) <= 40
    assert prompt.startswith("Termes : ")


# ── correct_text ───────────────────────────────────────────────────────────────

def test_correct_text_corrige_quasi_erreur():
    dictionary.add_term("KKiaPay")
    # "kiapay" est très proche de "KKiaPay" (ratio ≈ 0.92) → corrigé.
    assert dictionary.correct_text("j'utilise kiapay demain") == "j'utilise KKiaPay demain"


def test_correct_text_preserve_ponctuation():
    dictionary.add_term("KKiaPay")
    assert dictionary.correct_text("paiement via kiapay.") == "paiement via KKiaPay."


def test_correct_text_ne_touche_pas_un_mot_correct():
    dictionary.add_term("KKiaPay")
    # Le mot est déjà exactement le terme → laissé intact (pas de « re-correction »).
    assert dictionary.correct_text("réglé avec KKiaPay") == "réglé avec KKiaPay"


def test_correct_text_ne_corrige_pas_mot_eloigne():
    dictionary.add_term("KKiaPay")
    # "maison" est trop loin de "KKiaPay" (ratio < 0.82) → intact.
    phrase = "la maison est grande"
    assert dictionary.correct_text(phrase) == phrase


def test_correct_text_ne_corrige_pas_mot_normal():
    dictionary.add_term("OHADA")
    phrase = "je vais au bureau ce matin"
    assert dictionary.correct_text(phrase) == phrase


def test_correct_text_liste_vide_inchange():
    phrase = "aucun terme enregistre ici kiapay ohada"
    assert dictionary.correct_text(phrase) == phrase


def test_correct_text_vide():
    dictionary.add_term("Voxaho")
    assert dictionary.correct_text("") == ""


def test_correct_text_conservateur_sur_phrase_reelle():
    dictionary.add_term("Voxaho")
    dictionary.add_term("KKiaPay")
    # Seul le mot fautif est remplacé, le reste de la phrase est intact.
    src = "Bonjour, je teste voxao avec kiapay aujourd'hui"
    out = dictionary.correct_text(src)
    assert "Voxaho" in out
    assert "KKiaPay" in out
    assert out.startswith("Bonjour, je teste ")
    assert out.endswith(" aujourd'hui")
