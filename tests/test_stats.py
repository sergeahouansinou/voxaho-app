"""
Tests de core.stats : agrégats d'usage calculés depuis l'historique.

Couvre le cas base vide (zéros, aucune exception) puis avec quelques dictées
(total, moyenne, temps gagné, compteurs du jour) et la série words_per_day().

Isolation totale : base SQLite neuve dans tmp_path via core.db.set_db_path().

Lancement : ./venv/bin/python -m pytest tests/test_stats.py -v
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import db, history, stats


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.set_db_path(str(tmp_path / "test.db"))
    yield
    db.set_db_path(db.DB_PATH)


# ── base vide ────────────────────────────────────────────────────────────────

def test_get_stats_base_vide():
    """Aucune dictée : tout à zéro, first_use_date None, pas de division par zéro."""
    s = stats.get_stats()
    assert s["total_dictations"] == 0
    assert s["total_words"] == 0
    assert s["total_chars"] == 0
    assert s["avg_words_per_dictation"] == 0.0
    assert s["today_dictations"] == 0
    assert s["today_words"] == 0
    assert s["first_use_date"] is None
    assert s["time_saved_minutes"] == 0.0


# ── avec des dictées ───────────────────────────────────────────────────────────

def test_get_stats_avec_entrees():
    # 3 dictées, mots explicites pour un total maîtrisé.
    history.add_entry("un deux trois", word_count=3)
    history.add_entry("quatre cinq", word_count=2)
    history.add_entry("six sept huit neuf dix", word_count=5)

    s = stats.get_stats()
    assert s["total_dictations"] == 3
    assert s["total_words"] == 10
    assert s["avg_words_per_dictation"] == round(10 / 3, 1)
    # total_chars = somme des longueurs de texte.
    expected_chars = len("un deux trois") + len("quatre cinq") + len("six sept huit neuf dix")
    assert s["total_chars"] == expected_chars
    # Les 3 dictées viennent d'être créées → aujourd'hui (UTC).
    assert s["today_dictations"] == 3
    assert s["today_words"] == 10
    assert s["first_use_date"] is not None


def test_time_saved_coherent():
    # 150 mots : 150 * (1/40 - 1/150) = 2.75 min, arrondi à 1 décimale → 2.8.
    history.add_entry("x " * 150, word_count=150)
    s = stats.get_stats()
    assert s["time_saved_minutes"] == 2.8


def test_today_ignore_les_jours_passes():
    # Une dictée d'aujourd'hui + une insérée manuellement hier.
    history.add_entry("aujourd hui", word_count=2)
    # Insertion directe d'une entrée datée d'hier (contourne _now_iso).
    yesterday = "2020-01-01T12:00:00+00:00"
    from contextlib import closing
    with closing(db.connect()) as conn:
        conn.execute(
            "INSERT INTO history (text, created_at, word_count) VALUES (?, ?, ?)",
            ("vieille dictee", yesterday, 100),
        )
        conn.commit()

    s = stats.get_stats()
    assert s["total_dictations"] == 2
    assert s["total_words"] == 102
    # Le jour ne compte que la dictée d'aujourd'hui.
    assert s["today_dictations"] == 1
    assert s["today_words"] == 2
    # first_use_date = la plus ancienne (2020).
    assert s["first_use_date"] == yesterday


# ── words_per_day ──────────────────────────────────────────────────────────────

def test_words_per_day_longueur_et_ordre():
    res = stats.words_per_day(days=7)
    assert len(res) == 7
    # Dates strictement croissantes (du plus ancien au plus récent).
    dates = [r["date"] for r in res]
    assert dates == sorted(dates)
    # Le dernier élément est aujourd'hui (UTC).
    today = datetime.now(timezone.utc).date().isoformat()
    assert dates[-1] == today
    # Base vide : tous à 0.
    assert all(r["words"] == 0 for r in res)


def test_words_per_day_compte_aujourdhui():
    history.add_entry("mots du jour", word_count=12)
    res = stats.words_per_day(days=30)
    assert len(res) == 30
    # Le dernier jour (aujourd'hui) porte les mots insérés.
    assert res[-1]["words"] == 12
    # Les jours précédents restent à 0.
    assert all(r["words"] == 0 for r in res[:-1])


def test_words_per_day_zero():
    assert stats.words_per_day(days=0) == []
