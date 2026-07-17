"""
Statistiques d'usage — calculées à la volée depuis la table `history`.

Aucune donnée dédiée n'est stockée : tout est agrégé depuis l'historique des
dictées. Robuste au cas où la base est vide (aucune division par zéro).

Les dates « aujourd'hui » et celles de words_per_day() sont calculées en UTC,
pour rester cohérentes avec les horodatages stockés (core.db._now_iso() = UTC).
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone

from core import db

# Cadences de référence pour l'estimation du temps gagné.
# Dicter ≈ 150 mots/min, taper au clavier ≈ 40 mots/min.
_WORDS_PER_MIN_SPEAKING = 150
_WORDS_PER_MIN_TYPING = 40


def _today_utc() -> str:
    """Date du jour en UTC, format 'YYYY-MM-DD' (préfixe des created_at)."""
    return datetime.now(timezone.utc).date().isoformat()


# ── Statistiques globales ──────────────────────────────────────────────────────

def get_stats() -> dict:
    """Retourne un dict de statistiques agrégées depuis l'historique.

    Clés : total_dictations, total_words, total_chars, avg_words_per_dictation,
    today_dictations, today_words, first_use_date, time_saved_minutes.

    time_saved_minutes : temps gagné en dictant plutôt qu'en tapant. Pour
    total_words mots :
        minutes = total_words * (1/40 - 1/150)
    (soit ≈ 0,0183 min gagnée par mot), arrondi à 1 décimale.
    """
    today = _today_utc()
    with closing(db.connect()) as conn:
        row = conn.execute(
            "SELECT "
            "  COUNT(*)                       AS total_dictations, "
            "  COALESCE(SUM(word_count), 0)   AS total_words, "
            "  COALESCE(SUM(LENGTH(text)), 0) AS total_chars, "
            "  MIN(created_at)                AS first_use_date "
            "FROM history"
        ).fetchone()

        today_row = conn.execute(
            "SELECT "
            "  COUNT(*)                     AS today_dictations, "
            "  COALESCE(SUM(word_count), 0) AS today_words "
            "FROM history WHERE substr(created_at, 1, 10) = ?",
            (today,),
        ).fetchone()

    total_dictations = row["total_dictations"]
    total_words = row["total_words"]

    avg_words = round(total_words / total_dictations, 1) if total_dictations else 0.0

    # Temps gagné : différence entre le temps de frappe et le temps de dictée.
    time_saved = round(
        total_words * (1 / _WORDS_PER_MIN_TYPING - 1 / _WORDS_PER_MIN_SPEAKING), 1
    )

    return {
        "total_dictations": total_dictations,
        "total_words": total_words,
        "total_chars": row["total_chars"],
        "avg_words_per_dictation": avg_words,
        "today_dictations": today_row["today_dictations"],
        "today_words": today_row["today_words"],
        "first_use_date": row["first_use_date"],  # None si base vide
        "time_saved_minutes": time_saved,
    }


# ── Série temporelle (futur graphe) ────────────────────────────────────────────

def words_per_day(days: int = 30) -> list[dict]:
    """Mots dictés par jour sur les `days` derniers jours (UTC).

    Retourne [{"date": "YYYY-MM-DD", "words": int}] de longueur `days`, du plus
    ancien au plus récent. Les jours sans dictée valent 0.
    """
    if days <= 0:
        return []

    # Agrégation en une requête : mots par date de création.
    with closing(db.connect()) as conn:
        rows = conn.execute(
            "SELECT substr(created_at, 1, 10) AS d, "
            "       COALESCE(SUM(word_count), 0) AS words "
            "FROM history GROUP BY d"
        ).fetchall()
    by_date = {r["d"]: r["words"] for r in rows}

    today = datetime.now(timezone.utc).date()
    result = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        result.append({"date": day, "words": int(by_date.get(day, 0))})
    return result
