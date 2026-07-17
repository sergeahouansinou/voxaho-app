"""
Notes vocales — stockage 100 % local (table `notes`).

Une note a un titre, un corps, une date de création et une date de dernière
modification. Le champ updated_at est rafraîchi à chaque update_note().

Comme le reste de la couche de données, chaque fonction ouvre sa propre
connexion courte (voir core.db) puis la referme.
"""

from contextlib import closing

from core import db


# ── Conversion Row → dict ──────────────────────────────────────────────────────

def _note_to_dict(row) -> dict:
    return dict(row)


# ── Écriture ────────────────────────────────────────────────────────────────

def create_note(title: str = "", body: str = "") -> int:
    """Crée une note et retourne son id. created_at == updated_at à la création."""
    now = db._now_iso()
    with closing(db.connect()) as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, body, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (title, body, now, now),
        )
        conn.commit()
        return cur.lastrowid


def update_note(note_id: int, *, title: str | None = None, body: str | None = None) -> None:
    """Met à jour la note et rafraîchit updated_at.

    Seuls les champs explicitement fournis (non None) sont modifiés ; les autres
    sont laissés intacts. updated_at est toujours rafraîchi.
    """
    sets = ["updated_at = ?"]
    params: list = [db._now_iso()]
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if body is not None:
        sets.append("body = ?")
        params.append(body)
    params.append(note_id)

    with closing(db.connect()) as conn:
        conn.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()


# ── Lecture ─────────────────────────────────────────────────────────────────

def list_notes() -> list[dict]:
    """Liste les notes, la plus récemment modifiée d'abord (updated_at DESC)."""
    with closing(db.connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM notes ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    return [_note_to_dict(r) for r in rows]


def get_note(note_id: int) -> dict | None:
    """Retourne la note d'id `note_id`, ou None si absente."""
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    return _note_to_dict(row) if row is not None else None


# ── Suppression ─────────────────────────────────────────────────────────────

def delete_note(note_id: int) -> None:
    """Supprime définitivement la note d'id `note_id`."""
    with closing(db.connect()) as conn:
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
