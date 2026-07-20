"""
Profils par application — réglages de dictée automatiques selon l'app active (Phase 3b).

Un profil associe un MOTIF d'application (ex. « Code », « Mail », « chrome ») à
un jeu de surcharges de réglages (langue, modèle, reformatage, IA, traduction).
Au démarrage de chaque dictée, la barre flottante détecte l'app au premier plan
(core.appcontext.active_app), résout le profil correspondant, et applique les
réglages EFFECTIFS = défauts de la config + surcharges du profil. Sans profil
correspondant, on retombe intégralement sur les défauts de la config.

C'est le différenciateur maison face aux concurrents : « dans VS Code → anglais,
pas d'IA ; dans Mail → français, reformatage IA ». 100 % local.

Stockage : comme le reste de la couche de données (core.snippets, core.dictionary),
chaque fonction ouvre sa PROPRE connexion courte (voir core.db) puis la referme,
et la table est créée PARESSEUSEMENT (`CREATE TABLE IF NOT EXISTS` via _ensure()) :
on ne modifie donc PAS core.db.ensure_schema().

Sémantique des colonnes de réglage NULL : « ne pas surcharger, garder le défaut
de la config ». Une colonne renseignée surcharge le défaut correspondant.
"""

from contextlib import closing

from core import db
from core import appcontext


# ── Schéma paresseux ─────────────────────────────────────────────────────────

def _ensure(conn) -> None:
    """Crée la table `profiles` si absente (idempotent). Appelée en début d'op.

    Volontairement séparée de core.db.ensure_schema() : ce module possède sa
    propre table et la crée lui-même, sans toucher au schéma partagé.

    Les colonnes de réglage (language, model, reformatting, ai_reformat,
    translate_to) sont NULLABLES : NULL = « garder le défaut de la config ».
    reformatting / ai_reformat sont stockés en INTEGER (0/1) ou NULL.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT,
            app_pattern  TEXT    NOT NULL,
            language     TEXT,
            model        TEXT,
            reformatting INTEGER,
            ai_reformat  INTEGER,
            translate_to TEXT,
            created_at   TEXT    NOT NULL
        )
        """
    )


# Colonnes de réglage surchargeables par un profil (ordre stable).
_SETTING_FIELDS = ("language", "model", "reformatting", "ai_reformat", "translate_to")
# Sous-ensemble stocké en INTEGER (booléens 0/1) — converti à la lecture.
_BOOL_FIELDS = ("reformatting", "ai_reformat")


def _to_int_or_none(value):
    """Normalise un booléen surchargeable pour le stockage : None reste None,
    toute autre valeur devient 0/1 (INTEGER)."""
    return None if value is None else int(bool(value))


# ── Écriture ─────────────────────────────────────────────────────────────────

def add_profile(*, name: str | None = None, app_pattern: str,
                language: str | None = None, model: str | None = None,
                reformatting: bool | None = None,
                ai_reformat: bool | None = None,
                translate_to: str | None = None) -> int:
    """Crée un profil et retourne son id (ou -1 si le motif est vide).

    `app_pattern` est OBLIGATOIRE (motif d'application) et trimé ; vide après
    trim → aucune insertion, retourne -1. Les surcharges laissées à None ne
    surchargeront pas le défaut de la config au moment de la dictée.
    """
    app_pattern = (app_pattern or "").strip()
    if not app_pattern:
        return -1
    name = name.strip() if isinstance(name, str) else name
    with closing(db.connect()) as conn:
        _ensure(conn)
        cur = conn.execute(
            "INSERT INTO profiles "
            "(name, app_pattern, language, model, reformatting, ai_reformat, "
            " translate_to, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name, app_pattern, language, model,
                _to_int_or_none(reformatting), _to_int_or_none(ai_reformat),
                translate_to, db._now_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_profile(profile_id: int, **fields) -> None:
    """Met à jour les champs fournis du profil `profile_id`.

    Champs acceptés : name, app_pattern, language, model, reformatting,
    ai_reformat, translate_to. Seuls les champs EXPLICITEMENT présents dans
    `fields` sont modifiés (y compris à None = « repasser en défaut »). Les clés
    inconnues sont ignorées. app_pattern fourni est trimé ; s'il est vide après
    trim, il est ignoré (le motif ne doit jamais devenir vide).
    """
    allowed = ("name", "app_pattern") + _SETTING_FIELDS
    sets: list[str] = []
    params: list = []
    for key in allowed:
        if key not in fields:
            continue
        value = fields[key]
        if key == "app_pattern":
            value = (value or "").strip()
            if not value:
                continue  # motif obligatoire : on n'accepte pas de le vider
        elif key == "name" and isinstance(value, str):
            value = value.strip()
        elif key in _BOOL_FIELDS:
            value = _to_int_or_none(value)
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(profile_id)
    with closing(db.connect()) as conn:
        _ensure(conn)
        conn.execute(
            f"UPDATE profiles SET {', '.join(sets)} WHERE id = ?", params
        )
        conn.commit()


def remove_profile(profile_id: int) -> None:
    """Supprime définitivement le profil d'id `profile_id`."""
    with closing(db.connect()) as conn:
        _ensure(conn)
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.commit()


# ── Lecture ──────────────────────────────────────────────────────────────────

def list_profiles() -> list[dict]:
    """Liste les profils par ancienneté de création (id croissant).

    Retourne des dicts {id, name, app_pattern, language, model, reformatting,
    ai_reformat, translate_to, created_at}. reformatting / ai_reformat sont
    renvoyés en bool (ou None si non surchargé) pour un usage direct.
    """
    with closing(db.connect()) as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT id, name, app_pattern, language, model, reformatting, "
            "ai_reformat, translate_to, created_at "
            "FROM profiles ORDER BY id ASC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Ré-hydrate les booléens stockés en INTEGER (0/1) ou NULL.
        for key in _BOOL_FIELDS:
            d[key] = None if d[key] is None else bool(d[key])
        result.append(d)
    return result


# ── Résolution pour l'app active ─────────────────────────────────────────────

def resolve_for_app(app_name: str | None) -> dict | None:
    """Retourne le profil correspondant à `app_name`, ou None.

    Charge list_profiles() puis délègue la mise en correspondance à la fonction
    PURE appcontext.match_profile (premier motif sous-chaîne du nom d'app). On
    passe par le MODULE appcontext (et non par un import direct de la fonction)
    pour rester robuste au monkeypatch dans les tests.

    Défensif : toute erreur (base illisible, module absent…) → None. Ne LÈVE
    JAMAIS — appelée sur le chemin critique de la dictée.
    """
    try:
        profiles = list_profiles()
    except Exception:
        return None
    try:
        return appcontext.match_profile(app_name, profiles)
    except Exception:
        return None


def effective_settings(base_config: dict, profile: dict | None) -> dict:
    """Réglages EFFECTIFS = défauts de la config + surcharges du profil.

    Fonction PURE (aucun effet de bord) → totalement testable sans Qt ni base.

    Part des valeurs de base (language, model, reformatting, ai_reformat,
    translate_to) lues dans `base_config`, puis applique PAR-DESSUS chaque champ
    NON-None du profil. profile None → base inchangée (copie).

    NB sur translate_to : une colonne NULL signifie « garder le défaut » ; il n'y
    a donc pas de moyen, via un profil, de forcer la DÉSACTIVATION d'une
    traduction activée par défaut dans la config (limite assumée : les profils
    surchargent, ils ne suppriment pas un défaut).
    """
    eff = {
        "language":     base_config.get("language"),
        "model":        base_config.get("model"),
        "reformatting": base_config.get("reformatting"),
        "ai_reformat":  base_config.get("ai_reformat"),
        "translate_to": base_config.get("translate_to"),
    }
    if not profile:
        return eff
    for key in _SETTING_FIELDS:
        value = profile.get(key)
        if value is None:
            continue  # champ non surchargé → garder le défaut
        eff[key] = bool(value) if key in _BOOL_FIELDS else value
    return eff
