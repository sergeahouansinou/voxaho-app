"""
Historique des dictées — stockage 100 % local (table `history`).

Chaque dictée transcrite est enregistrée ici : texte, date, langue, modèle,
nombre de mots, durée d'enregistrement et flag « favori ». L'utilisateur peut
désactiver complètement l'historique (meta `history_enabled`) : dans ce cas
add_entry() n'insère rien et retourne -1.

Toutes les fonctions ouvrent leur propre connexion courte (voir core.db) et la
referment : c'est thread-safe pour le volume visé (le pipeline de dictée écrit
depuis un thread secondaire pendant que l'UI lit).
"""

from contextlib import closing

from core import db

# Clé meta stockant l'état d'activation de l'historique ("1" / "0").
_META_ENABLED = "history_enabled"


# ── Conversion Row → dict ──────────────────────────────────────────────────────

def _entry_to_dict(row) -> dict:
    """Convertit une ligne history en dict, avec `favorite` en booléen."""
    d = dict(row)
    d["favorite"] = bool(d.get("favorite"))
    return d


# ── Activation de l'historique ─────────────────────────────────────────────────

def is_enabled() -> bool:
    """True si l'historique est activé. Défaut True quand la clé meta est absente."""
    with closing(db.connect()) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (_META_ENABLED,)
        ).fetchone()
    if row is None:
        return True
    return row["value"] == "1"


def set_enabled(enabled: bool) -> None:
    """Active (True) ou désactive (False) l'enregistrement de l'historique."""
    with closing(db.connect()) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_META_ENABLED, "1" if enabled else "0"),
        )
        conn.commit()


# ── Écriture ────────────────────────────────────────────────────────────────

def add_entry(
    text: str,
    *,
    language: str | None = None,
    model: str | None = None,
    word_count: int | None = None,
    duration_s: float | None = None,
) -> int:
    """Ajoute une dictée à l'historique et retourne son id.

    Si l'historique est désactivé (is_enabled() == False), n'insère RIEN et
    retourne -1. word_count est calculé (len(text.split())) s'il n'est pas fourni.
    """
    if not is_enabled():
        return -1

    if word_count is None:
        word_count = len(text.split())

    with closing(db.connect()) as conn:
        cur = conn.execute(
            "INSERT INTO history "
            "(text, created_at, language, model, word_count, duration_s) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (text, db._now_iso(), language, model, word_count, duration_s),
        )
        conn.commit()
        return cur.lastrowid


# ── Lecture ─────────────────────────────────────────────────────────────────

def list_entries(
    *,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    favorites_only: bool = False,
) -> list[dict]:
    """Liste les dictées, les plus récentes d'abord (created_at DESC).

    - search : filtre LIKE insensible à la casse sur le texte.
    - favorites_only : ne garde que les favoris.
    Retourne des dicts (favorite en booléen).
    """
    clauses = []
    params: list = []
    if search:
        clauses.append("LOWER(text) LIKE LOWER(?)")
        params.append(f"%{search}%")
    if favorites_only:
        clauses.append("favorite = 1")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT * FROM history {where} "
        "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    with closing(db.connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_entry_to_dict(r) for r in rows]


def get_entry(entry_id: int) -> dict | None:
    """Retourne la dictée d'id `entry_id`, ou None si absente."""
    with closing(db.connect()) as conn:
        row = conn.execute(
            "SELECT * FROM history WHERE id = ?", (entry_id,)
        ).fetchone()
    return _entry_to_dict(row) if row is not None else None


# ── Modification / suppression ─────────────────────────────────────────────────

def toggle_favorite(entry_id: int) -> bool:
    """Inverse le flag favori de la dictée et retourne le NOUVEL état.

    Retourne False si l'entrée n'existe pas.
    """
    with closing(db.connect()) as conn:
        row = conn.execute(
            "SELECT favorite FROM history WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return False
        new_state = 0 if row["favorite"] else 1
        conn.execute(
            "UPDATE history SET favorite = ? WHERE id = ?", (new_state, entry_id)
        )
        conn.commit()
        return bool(new_state)


def delete_entry(entry_id: int) -> None:
    """Supprime définitivement la dictée d'id `entry_id`."""
    with closing(db.connect()) as conn:
        conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))
        conn.commit()


def clear_all() -> int:
    """Vide tout l'historique et retourne le nombre d'entrées supprimées."""
    with closing(db.connect()) as conn:
        count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        conn.execute("DELETE FROM history")
        conn.commit()
    return count
