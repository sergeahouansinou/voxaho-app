"""
Tests de core.notes : CRUD des notes vocales et rafraîchissement de updated_at.

Isolation totale : base SQLite neuve dans tmp_path via core.db.set_db_path().

Lancement : ./venv/bin/python -m pytest tests/test_notes.py -v
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import db, notes


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.set_db_path(str(tmp_path / "test.db"))
    yield
    db.set_db_path(db.DB_PATH)


# ── création / lecture ─────────────────────────────────────────────────────────

def test_create_note_defauts():
    note_id = notes.create_note()
    assert note_id > 0
    note = notes.get_note(note_id)
    assert note["title"] == ""
    assert note["body"] == ""
    # À la création, created_at == updated_at.
    assert note["created_at"] == note["updated_at"]


def test_create_note_avec_contenu():
    note_id = notes.create_note(title="Idées", body="corps de la note")
    note = notes.get_note(note_id)
    assert note["title"] == "Idées"
    assert note["body"] == "corps de la note"


def test_get_note_inexistant():
    assert notes.get_note(9999) is None


def test_list_notes_ordre_updated_desc():
    id1 = notes.create_note(title="A")
    time.sleep(0.01)
    id2 = notes.create_note(title="B")
    time.sleep(0.01)
    id3 = notes.create_note(title="C")

    listed = notes.list_notes()
    assert [n["id"] for n in listed] == [id3, id2, id1]


# ── mise à jour ────────────────────────────────────────────────────────────────

def test_update_note_change_updated_at():
    note_id = notes.create_note(title="titre", body="corps")
    before = notes.get_note(note_id)
    time.sleep(0.01)

    notes.update_note(note_id, body="corps modifie")
    after = notes.get_note(note_id)

    assert after["body"] == "corps modifie"
    assert after["title"] == "titre"          # champ non fourni : inchangé
    assert after["updated_at"] > before["updated_at"]
    assert after["created_at"] == before["created_at"]  # création inchangée


def test_update_note_champs_partiels():
    note_id = notes.create_note(title="ancien titre", body="ancien corps")
    notes.update_note(note_id, title="nouveau titre")
    note = notes.get_note(note_id)
    assert note["title"] == "nouveau titre"
    assert note["body"] == "ancien corps"  # non touché


def test_update_note_remonte_dans_la_liste():
    id1 = notes.create_note(title="A")
    time.sleep(0.01)
    id2 = notes.create_note(title="B")
    time.sleep(0.01)
    # On modifie la plus ancienne : elle doit repasser en tête.
    notes.update_note(id1, body="mise a jour")
    listed = notes.list_notes()
    assert listed[0]["id"] == id1


# ── suppression ────────────────────────────────────────────────────────────────

def test_delete_note():
    id1 = notes.create_note(title="a supprimer")
    id2 = notes.create_note(title="a garder")
    notes.delete_note(id1)
    assert notes.get_note(id1) is None
    assert notes.get_note(id2) is not None
    assert len(notes.list_notes()) == 1
