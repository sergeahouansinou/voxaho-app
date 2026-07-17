"""
Dictionnaire personnel — termes, noms propres et jargon de l'utilisateur (table `dictionary`).

Deux usages, 100 % locaux :
1. whisper_prompt() : concatène les termes en une phrase d'amorce passée à
   l'`initial_prompt` de Whisper → biaise la reconnaissance vers ces termes
   (ex. « KKiaPay », « OHADA », « Voxaho »).
2. correct_text() : corrige a posteriori les quasi-occurrences des termes connus
   (fautes de reconnaissance) via difflib, de façon CONSERVATRICE.

Comme le reste de la couche de données, chaque fonction ouvre sa propre
connexion courte (voir core.db) puis la referme. La table est créée
PARESSEUSEMENT (`CREATE TABLE IF NOT EXISTS` via _ensure()) à chaque opération :
on ne modifie donc PAS ensure_schema() de core.db.
"""

import re
from contextlib import closing
from difflib import SequenceMatcher

from core import db

# Seuil de similarité pour correct_text() — difflib SequenceMatcher.ratio().
# 0.82 est volontairement CONSERVATEUR : on ne remplace un mot du texte que s'il
# est TRÈS proche d'un terme connu (typiquement une ou deux lettres d'écart sur
# un mot de longueur moyenne, ex. « kiapay » ≈ « KKiaPay »). En dessous de ce
# seuil, la ressemblance est jugée trop lointaine et le mot est laissé intact —
# jamais de sur-correction agressive.
_CORRECTION_THRESHOLD = 0.82


# ── Schéma paresseux ───────────────────────────────────────────────────────────

def _ensure(conn) -> None:
    """Crée la table `dictionary` si absente (idempotent). Appelé en début d'op.

    Volontairement séparé de core.db.ensure_schema() : ce module possède sa
    propre table et la crée lui-même, sans toucher au schéma partagé.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dictionary (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            term        TEXT    NOT NULL UNIQUE,
            created_at  TEXT    NOT NULL
        )
        """
    )


# ── Écriture ────────────────────────────────────────────────────────────────

def add_term(term: str) -> int:
    """Ajoute un terme (après trim) et retourne son id.

    Le doublon exact est ignoré (INSERT OR IGNORE) : dans ce cas on retourne
    l'id du terme DÉJÀ présent. Un terme vide (après trim) n'insère rien et
    retourne -1.
    """
    term = term.strip()
    if not term:
        return -1
    with closing(db.connect()) as conn:
        _ensure(conn)
        conn.execute(
            "INSERT OR IGNORE INTO dictionary (term, created_at) VALUES (?, ?)",
            (term, db._now_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM dictionary WHERE term = ?", (term,)
        ).fetchone()
        return row["id"] if row is not None else -1


def remove_term(term_id: int) -> None:
    """Supprime définitivement le terme d'id `term_id`."""
    with closing(db.connect()) as conn:
        _ensure(conn)
        conn.execute("DELETE FROM dictionary WHERE id = ?", (term_id,))
        conn.commit()


# ── Lecture ─────────────────────────────────────────────────────────────────

def list_terms() -> list[dict]:
    """Liste les termes par ordre alphabétique (insensible à la casse).

    Retourne des dicts {id, term, created_at}.
    """
    with closing(db.connect()) as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT id, term, created_at FROM dictionary "
            "ORDER BY term COLLATE NOCASE ASC, id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Amorce Whisper ────────────────────────────────────────────────────────────

def whisper_prompt(max_chars: int = 200) -> str:
    """Concatène les termes en une phrase d'amorce pour l'`initial_prompt` de Whisper.

    Ex. « Termes : Voxaho, KKiaPay, OHADA. ». Le résultat est tronqué à
    `max_chars` caractères (garantie : len(résultat) <= max_chars). Aucun terme
    enregistré → chaîne vide "".
    """
    terms = [t["term"] for t in list_terms()]
    if not terms:
        return ""
    prompt = "Termes : " + ", ".join(terms) + "."
    if len(prompt) > max_chars:
        # Troncature dure à max_chars, puis nettoyage d'une éventuelle fin
        # d'énumération coupée (espace ou virgule résiduels).
        prompt = prompt[:max_chars].rstrip(" ,")
    return prompt


# ── Correction post-transcription ──────────────────────────────────────────────

def correct_text(text: str) -> str:
    """Corrige les quasi-occurrences des termes connus (fautes de reconnaissance).

    Fonctionnement, mot à mot (word-level) et CONSERVATEUR :
    - chaque mot du texte est comparé (insensible à la casse) à chaque terme
      connu via difflib.SequenceMatcher.ratio() ;
    - un mot n'est remplacé par le terme (dans sa casse canonique enregistrée)
      QUE si le meilleur ratio atteint _CORRECTION_THRESHOLD (0.82) ET que le mot
      diffère déjà du terme (à la casse près) — un mot déjà correct (égal à un
      terme, casse ignorée) n'est jamais touché ;
    - tout ce qui entoure le mot (espaces, ponctuation, casse de la phrase) est
      préservé : seul le mot fautif est substitué.

    Robuste : texte vide ou aucun terme enregistré → texte renvoyé inchangé.
    """
    if not text:
        return text
    terms = [t["term"] for t in list_terms()]
    if not terms:
        return text

    # Pré-calcul des versions minuscules des termes (comparaison insensible casse).
    lowered = [(term, term.lower()) for term in terms]

    def _best_match(word_lower: str):
        """Retourne (terme, ratio) du terme le plus proche du mot (minuscule)."""
        best_term = None
        best_ratio = 0.0
        for term, term_lower in lowered:
            ratio = SequenceMatcher(None, word_lower, term_lower).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_term = term
        return best_term, best_ratio

    def _replace(match: "re.Match") -> str:
        word = match.group(0)
        # On ne corrige que des mots contenant au moins une lettre (on ignore les
        # nombres purs et séquences sans alpha — pas de terme plausible).
        if not any(c.isalpha() for c in word):
            return word
        word_lower = word.lower()
        best_term, best_ratio = _best_match(word_lower)
        if best_term is None:
            return word
        # Mot déjà correct (identique au terme à la casse près) → intact.
        if word_lower == best_term.lower():
            return word
        # Assez proche → on substitue par le terme dans sa casse canonique.
        if best_ratio >= _CORRECTION_THRESHOLD:
            return best_term
        return word

    # \w+ (Unicode) isole les « mots » ; tout le reste (espaces, ponctuation) est
    # laissé tel quel par re.sub → la structure de la phrase est préservée.
    return re.sub(r"\w+", _replace, text, flags=re.UNICODE)
