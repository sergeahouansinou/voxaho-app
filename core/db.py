"""
Connexion et schéma SQLite — fondation de la couche de données locale.

Tout Voxaho stocke son historique de dictées, ses notes vocales et ses
statistiques d'usage dans UNE seule base SQLite : ~/.voxaho/voxaho.db.
Aucune donnée ne quitte la machine — c'est l'ADN du produit.

Concurrence :
  Le pipeline de dictée écrit depuis un thread secondaire pendant que l'UI
  lit depuis le thread principal. On ouvre donc la base en mode WAL
  (Write-Ahead Logging) qui autorise lecteurs et écrivain simultanés, et
  chaque fonction publique des modules history/notes/stats ouvre sa PROPRE
  connexion courte (connect-per-call) puis la ferme. C'est simple, robuste
  et suffisant pour ce volume.

Tests :
  set_db_path(path) redirige la base vers un fichier temporaire. Les tests
  monkeypatchent ce chemin pour partir d'une base neuve à chaque fois.
"""

import os
import sqlite3
from datetime import datetime, timezone

# Chemin par défaut de la base, dans le dossier de config de Voxaho.
DB_PATH = os.path.expanduser("~/.voxaho/voxaho.db")

# Chemin effectif utilisé par connect(). Modifiable via set_db_path() — les
# tests le redirigent vers un fichier temporaire pour repartir d'une base neuve.
_DB_PATH = DB_PATH


# ── Chemin de la base ───────────────────────────────────────────────────────

def set_db_path(path: str) -> None:
    """Redirige la base vers `path`. Utilisé par les tests (base temporaire)."""
    global _DB_PATH
    _DB_PATH = path


def get_db_path() -> str:
    """Chemin effectif de la base actuellement utilisée par connect()."""
    return _DB_PATH


# ── Connexion ───────────────────────────────────────────────────────────────

def connect() -> sqlite3.Connection:
    """Ouvre une connexion prête à l'emploi vers la base (schéma garanti).

    - check_same_thread=False : la connexion peut être créée dans un thread et
      utilisée dans un autre (on ouvre de toute façon une connexion par appel).
    - row_factory=sqlite3.Row : accès aux colonnes par nom.
    - journal_mode=WAL : concurrence lecture/écriture.
    Le dossier parent est créé au besoin, puis ensure_schema() est appelé.
    """
    parent = os.path.dirname(_DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


# ── Schéma ──────────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    """Crée les tables et index si absents. Idempotent (IF NOT EXISTS)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            text        TEXT    NOT NULL,
            created_at  TEXT    NOT NULL,
            language    TEXT,
            model       TEXT,
            word_count  INTEGER,
            duration_s  REAL,
            favorite    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT,
            body        TEXT,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS meta (
            key         TEXT PRIMARY KEY,
            value       TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_history_created_at ON history(created_at);
        CREATE INDEX IF NOT EXISTS idx_history_favorite   ON history(favorite);
        """
    )
    conn.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Horodatage courant en ISO 8601 UTC (ex. 2026-07-17T12:34:56.789+00:00)."""
    return datetime.now(timezone.utc).isoformat()
