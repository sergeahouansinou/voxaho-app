"""
Tests de core.snippets : CRUD des snippets vocaux et expansion des déclencheurs
dans un texte (insensible à la casse, longs d'abord, ponctuation gérée).

Isolation totale : base SQLite neuve dans tmp_path via core.db.set_db_path().

Lancement : ./venv/bin/python -m pytest tests/test_snippets.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import db, snippets


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.set_db_path(str(tmp_path / "t.db"))
    yield
    db.set_db_path(db.DB_PATH)


# ── CRUD ───────────────────────────────────────────────────────────────────────

def test_add_et_list():
    sid = snippets.add_snippet("ma signature", "Cordialement, Serge")
    assert sid > 0
    listed = snippets.list_snippets()
    assert len(listed) == 1
    assert listed[0]["trigger"] == "ma signature"
    assert listed[0]["expansion"] == "Cordialement, Serge"
    assert listed[0]["id"] == sid
    assert listed[0]["created_at"]


def test_add_trim_trigger():
    snippets.add_snippet("   mon adresse  ", "10 rue des Lilas")
    assert snippets.list_snippets()[0]["trigger"] == "mon adresse"


def test_add_trigger_vide_retourne_moins_un():
    assert snippets.add_snippet("   ", "peu importe") == -1
    assert snippets.list_snippets() == []


def test_add_doublon_trigger_ignore():
    id1 = snippets.add_snippet("sig", "Version 1")
    id2 = snippets.add_snippet("sig", "Version 2")
    assert id1 == id2
    listed = snippets.list_snippets()
    assert len(listed) == 1
    # L'expansion d'origine est conservée (add ne remplace pas un doublon).
    assert listed[0]["expansion"] == "Version 1"


def test_update_snippet():
    sid = snippets.add_snippet("sig", "ancienne")
    snippets.update_snippet(sid, expansion="nouvelle")
    assert snippets.list_snippets()[0]["expansion"] == "nouvelle"
    snippets.update_snippet(sid, trigger="  signature  ")
    row = snippets.list_snippets()[0]
    assert row["trigger"] == "signature"      # trimé
    assert row["expansion"] == "nouvelle"     # inchangé


def test_update_snippet_sans_champ_ne_fait_rien():
    sid = snippets.add_snippet("sig", "expansion")
    snippets.update_snippet(sid)  # aucun champ → no-op
    assert snippets.list_snippets()[0]["expansion"] == "expansion"


def test_remove_snippet():
    id1 = snippets.add_snippet("a", "AAA")
    snippets.add_snippet("b", "BBB")
    snippets.remove_snippet(id1)
    triggers = [s["trigger"] for s in snippets.list_snippets()]
    assert triggers == ["b"]


def test_list_ordre_alpha_insensible_casse():
    snippets.add_snippet("Zoulou", "z")
    snippets.add_snippet("alpha", "a")
    snippets.add_snippet("Mike", "m")
    triggers = [s["trigger"] for s in snippets.list_snippets()]
    assert triggers == ["alpha", "Mike", "Zoulou"]


# ── expand_text ──────────────────────────────────────────────────────────────

def test_expand_simple():
    snippets.add_snippet("ma signature", "Cordialement, Serge")
    assert snippets.expand_text("voici ma signature") == "voici Cordialement, Serge"


def test_expand_insensible_casse():
    snippets.add_snippet("ma signature", "Cordialement")
    assert snippets.expand_text("Voici Ma Signature ici") == "Voici Cordialement ici"


def test_expand_ponctuation_autour():
    snippets.add_snippet("ma signature", "Cordialement")
    assert snippets.expand_text("fin: ma signature.") == "fin: Cordialement."
    assert snippets.expand_text("(ma signature)") == "(Cordialement)"


def test_expand_ne_touche_pas_sous_chaine_dans_un_mot():
    snippets.add_snippet("sig", "SIGNATURE")
    # "sig" est un préfixe de "signal" mais pas un mot entier → intact.
    assert snippets.expand_text("le signal est fort") == "le signal est fort"


def test_expand_plusieurs_snippets():
    snippets.add_snippet("bonjour", "Salutations")
    snippets.add_snippet("merci", "Grand merci")
    assert snippets.expand_text("bonjour et merci") == "Salutations et Grand merci"


def test_expand_longs_declencheurs_dabord():
    snippets.add_snippet("bonjour", "COURT")
    snippets.add_snippet("bonjour le monde", "LONG")
    # Le déclencheur long doit primer : pas de "COURT le monde".
    assert snippets.expand_text("bonjour le monde") == "LONG"
    # Le court reste actif quand il est seul.
    assert snippets.expand_text("bonjour toi") == "COURT toi"


def test_expand_aucun_snippet_inchange():
    assert snippets.expand_text("texte quelconque") == "texte quelconque"


def test_expand_texte_vide():
    snippets.add_snippet("sig", "X")
    assert snippets.expand_text("") == ""


def test_expand_expansion_avec_antislash_non_interprete():
    # L'expansion est insérée telle quelle, sans interprétation de type \1.
    snippets.add_snippet("ref", r"voir \1 et \g<0>")
    assert snippets.expand_text("ref maintenant") == r"voir \1 et \g<0> maintenant"
