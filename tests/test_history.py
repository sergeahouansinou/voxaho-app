"""
Tests de core.history : CRUD de l'historique des dictées, recherche, favoris
et flag d'activation.

Isolation totale : chaque test part d'une base SQLite NEUVE dans tmp_path via
core.db.set_db_path(). Aucune donnée ne touche ~/.voxaho.

Lancement : ./venv/bin/python -m pytest tests/test_history.py -v
"""

import sys
from pathlib import Path

# Rendre le package `core` importable quel que soit le mode d'invocation de pytest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import db, history


# ── Fixture : base neuve par test ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Redirige la base vers un fichier temporaire propre à chaque test."""
    db.set_db_path(str(tmp_path / "test.db"))
    yield
    db.set_db_path(db.DB_PATH)  # restaure le chemin par défaut


# ── add / list / get ───────────────────────────────────────────────────────────

def test_add_entry_retourne_id_et_calcule_word_count():
    entry_id = history.add_entry("bonjour le monde ceci est un test")
    assert entry_id > 0

    entry = history.get_entry(entry_id)
    assert entry is not None
    assert entry["text"] == "bonjour le monde ceci est un test"
    assert entry["word_count"] == 7  # calculé via len(text.split())
    assert entry["favorite"] is False
    assert entry["created_at"]  # horodatage non vide


def test_add_entry_metadonnees_explicites():
    entry_id = history.add_entry(
        "texte",
        language="fr",
        model="small",
        word_count=42,
        duration_s=3.5,
    )
    entry = history.get_entry(entry_id)
    assert entry["language"] == "fr"
    assert entry["model"] == "small"
    assert entry["word_count"] == 42  # non recalculé quand fourni
    assert entry["duration_s"] == 3.5


def test_get_entry_inexistant_retourne_none():
    assert history.get_entry(9999) is None


def test_list_entries_ordre_decroissant():
    id1 = history.add_entry("premiere")
    id2 = history.add_entry("deuxieme")
    id3 = history.add_entry("troisieme")

    entries = history.list_entries()
    assert len(entries) == 3
    # La plus récente d'abord.
    assert [e["id"] for e in entries] == [id3, id2, id1]


def test_list_entries_limit_offset():
    for i in range(5):
        history.add_entry(f"dictee {i}")

    page = history.list_entries(limit=2, offset=0)
    assert len(page) == 2
    page2 = history.list_entries(limit=2, offset=2)
    assert len(page2) == 2
    # Pas de chevauchement entre les pages.
    assert {e["id"] for e in page}.isdisjoint({e["id"] for e in page2})


# ── recherche ────────────────────────────────────────────────────────────────

def test_search_insensible_a_la_casse():
    history.add_entry("Bonjour tout le monde")
    history.add_entry("Rendez-vous demain")

    res = history.list_entries(search="BONJOUR")
    assert len(res) == 1
    assert res[0]["text"] == "Bonjour tout le monde"

    res2 = history.list_entries(search="demain")
    assert len(res2) == 1


def test_search_sans_resultat():
    history.add_entry("quelque chose")
    assert history.list_entries(search="introuvable") == []


# ── favoris ────────────────────────────────────────────────────────────────────

def test_toggle_favorite_bascule_etat():
    entry_id = history.add_entry("a marquer")
    assert history.toggle_favorite(entry_id) is True
    assert history.get_entry(entry_id)["favorite"] is True
    assert history.toggle_favorite(entry_id) is False
    assert history.get_entry(entry_id)["favorite"] is False


def test_toggle_favorite_inexistant():
    assert history.toggle_favorite(9999) is False


def test_favorites_only():
    id1 = history.add_entry("normal")
    id2 = history.add_entry("favori")
    history.toggle_favorite(id2)

    favs = history.list_entries(favorites_only=True)
    assert len(favs) == 1
    assert favs[0]["id"] == id2


# ── suppression ────────────────────────────────────────────────────────────────

def test_delete_entry():
    id1 = history.add_entry("a supprimer")
    id2 = history.add_entry("a garder")
    history.delete_entry(id1)

    assert history.get_entry(id1) is None
    assert history.get_entry(id2) is not None


def test_clear_all_retourne_nb_supprime():
    for i in range(3):
        history.add_entry(f"entree {i}")
    assert history.clear_all() == 3
    assert history.list_entries() == []
    # Sur base vide, retourne 0.
    assert history.clear_all() == 0


# ── activation de l'historique ──────────────────────────────────────────────────

def test_is_enabled_true_par_defaut():
    assert history.is_enabled() is True


def test_set_enabled_false_bloque_add_entry():
    history.set_enabled(False)
    assert history.is_enabled() is False

    result = history.add_entry("ne doit pas etre inseree")
    assert result == -1
    # Rien n'a été inséré.
    assert history.list_entries() == []


def test_set_enabled_reactivation():
    history.set_enabled(False)
    assert history.add_entry("bloquee") == -1
    history.set_enabled(True)
    new_id = history.add_entry("acceptee")
    assert new_id > 0
    assert len(history.list_entries()) == 1
