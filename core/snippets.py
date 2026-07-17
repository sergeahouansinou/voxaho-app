"""
Snippets vocaux — un déclencheur parlé est remplacé par un texte complet (table `snippets`).

Ex. déclencheur « ma signature » → expansion « Cordialement, Serge AHOUANSINOU… ».
100 % local. Utile pour insérer d'un mot une formule de politesse, une adresse,
un bloc juridique récurrent, etc.

Comme le reste de la couche de données, chaque fonction ouvre sa propre
connexion courte (voir core.db) puis la referme. La table est créée
PARESSEUSEMENT (`CREATE TABLE IF NOT EXISTS` via _ensure()) à chaque opération :
on ne modifie donc PAS ensure_schema() de core.db.
"""

import re
from contextlib import closing

from core import db


# ── Schéma paresseux ───────────────────────────────────────────────────────────

def _ensure(conn) -> None:
    """Crée la table `snippets` si absente (idempotent). Appelé en début d'op.

    Volontairement séparé de core.db.ensure_schema() : ce module possède sa
    propre table et la crée lui-même, sans toucher au schéma partagé.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snippets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger     TEXT    NOT NULL UNIQUE,
            expansion   TEXT    NOT NULL,
            created_at  TEXT    NOT NULL
        )
        """
    )


# ── Écriture ────────────────────────────────────────────────────────────────

def add_snippet(trigger: str, expansion: str) -> int:
    """Crée un snippet et retourne son id.

    Le déclencheur est trimé ; l'expansion est conservée telle quelle (elle peut
    contenir volontairement des espaces / sauts de ligne). Un déclencheur en
    doublon exact est ignoré (INSERT OR IGNORE) et on retourne l'id existant —
    l'expansion d'origine est alors laissée intacte (utiliser update_snippet pour
    la changer). Déclencheur vide (après trim) → aucune insertion, retourne -1.
    """
    trigger = trigger.strip()
    if not trigger:
        return -1
    with closing(db.connect()) as conn:
        _ensure(conn)
        conn.execute(
            "INSERT OR IGNORE INTO snippets (trigger, expansion, created_at) "
            "VALUES (?, ?, ?)",
            (trigger, expansion, db._now_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM snippets WHERE trigger = ?", (trigger,)
        ).fetchone()
        return row["id"] if row is not None else -1


def update_snippet(snippet_id: int, *, trigger: str | None = None,
                   expansion: str | None = None) -> None:
    """Met à jour le déclencheur et/ou l'expansion du snippet.

    Seuls les champs explicitement fournis (non None) sont modifiés ; les autres
    restent intacts. Le déclencheur fourni est trimé.
    """
    sets: list[str] = []
    params: list = []
    if trigger is not None:
        sets.append("trigger = ?")
        params.append(trigger.strip())
    if expansion is not None:
        sets.append("expansion = ?")
        params.append(expansion)
    if not sets:
        return
    params.append(snippet_id)
    with closing(db.connect()) as conn:
        _ensure(conn)
        conn.execute(
            f"UPDATE snippets SET {', '.join(sets)} WHERE id = ?", params
        )
        conn.commit()


def remove_snippet(snippet_id: int) -> None:
    """Supprime définitivement le snippet d'id `snippet_id`."""
    with closing(db.connect()) as conn:
        _ensure(conn)
        conn.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))
        conn.commit()


# ── Lecture ─────────────────────────────────────────────────────────────────

def list_snippets() -> list[dict]:
    """Liste les snippets par ordre alphabétique de déclencheur (insensible casse).

    Retourne des dicts {id, trigger, expansion, created_at}.
    """
    with closing(db.connect()) as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT id, trigger, expansion, created_at FROM snippets "
            "ORDER BY trigger COLLATE NOCASE ASC, id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Expansion post-transcription ───────────────────────────────────────────────

def expand_text(text: str) -> str:
    """Remplace dans `text` chaque déclencheur présent par son expansion.

    - Match sur mot / phrase ENTIER (bornes de mot tolérant la ponctuation
      autour), insensible à la casse.
    - Les déclencheurs les plus LONGS sont prioritaires : ils figurent en tête de
      l'alternation regex, ce qui évite qu'un déclencheur court n'« avale » une
      partie d'un plus long (chevauchement).
    - Passe UNIQUE : le texte inséré par une expansion n'est jamais ré-analysé,
      donc aucune expansion en cascade accidentelle.

    Robuste : texte vide ou aucun snippet → texte renvoyé inchangé.
    """
    if not text:
        return text
    snippets = list_snippets()
    if not snippets:
        return text

    # Déclencheurs valides (non vides après trim), triés du plus long au plus
    # court pour prioriser les longs dans l'alternation regex.
    valid = [(s["trigger"].strip(), s["expansion"]) for s in snippets]
    valid = [(trig, exp) for trig, exp in valid if trig]
    if not valid:
        return text
    valid.sort(key=lambda pair: len(pair[0]), reverse=True)

    # Table de correspondance déclencheur (minuscule) → expansion, et construction
    # de l'alternation. En cas de doublon insensible à la casse, on garde le
    # premier rencontré (donc le plus long, grâce au tri).
    mapping: dict[str, str] = {}
    parts: list[str] = []
    for trig, exp in valid:
        key = trig.lower()
        if key in mapping:
            continue
        mapping[key] = exp
        parts.append(re.escape(trig))

    pattern = re.compile(
        r"(?<!\w)(" + "|".join(parts) + r")(?!\w)",
        flags=re.IGNORECASE | re.UNICODE,
    )

    # Callback (et non chaîne de remplacement) : l'expansion est renvoyée telle
    # quelle, sans interprétation d'éventuels antislashs / références de groupe.
    def _sub(match: "re.Match") -> str:
        return mapping[match.group(1).lower()]

    return pattern.sub(_sub, text)
