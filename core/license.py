"""
Validation de licence Lemon Squeezy + trial 14 jours signé HMAC.

Flux licence :
  1. L'utilisateur achète sur voxaho.com → reçoit une clé par email
  2. Au premier lancement après achat, il colle la clé dans l'app
  3. activate(key) appelle l'API LS, qui retourne un instance_id (unique par machine)
  4. (key, instance_id) sont stockés dans ~/.voxaho/license.json
  5. Au démarrage suivant, validate() vérifie en ligne — avec tolérance hors-ligne 7 jours

Flux trial :
  - start_trial() écrit ~/.voxaho/trial.json signé HMAC-SHA256
  - trial_status() retourne l'état (active/expired, jours restants)
  - Protection anti-rollback : on stocke un "last_seen" signé, et on refuse si
    l'horloge système recule sous cette valeur.

API Lemon Squeezy : https://docs.lemonsqueezy.com/api/license-api
"""

import hashlib
import hmac
import json
import logging
import os
import platform
import socket
from datetime import datetime, timedelta, timezone

import urllib.request
import urllib.parse
import urllib.error

logger = logging.getLogger(__name__)

LS_API = "https://api.lemonsqueezy.com/v1/licenses"
LICENSE_PATH = os.path.expanduser("~/.voxaho/license.json")
TRIAL_PATH   = os.path.expanduser("~/.voxaho/trial.json")
OFFLINE_GRACE_DAYS = 7    # tolère 7 jours sans connexion avant de re-valider
REVALIDATE_HOURS = 72     # re-vérifie la licence en ligne au-delà de cet âge
TRIAL_DAYS = 14


def _default_marker_path() -> str:
    """Emplacement discret, dépendant de la plateforme, du marqueur trial redondant.

    Le marqueur est une copie signée de trial.json stockée hors de ~/.voxaho :
    supprimer ~/.voxaho/trial.json ne suffit donc plus à réinitialiser l'essai.
    """
    if platform.system() == "Darwin":
        return os.path.expanduser("~/Library/Application Support/.voxaho_meta")
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return os.path.join(appdata, ".voxaho_meta")
    return os.path.expanduser("~/.config/.voxaho_meta")


TRIAL_MARKER_PATH = _default_marker_path()

# Clé HMAC embarquée. Pas un secret cryptographique fort (binaire distribué),
# mais suffisant pour détecter une édition naïve du fichier trial.json.
_TRIAL_HMAC_KEY = b"voxaho-trial-v1-7f3c9a2e4d8b1c5f6e0a9b3d2c4e7f1a"


class LicenseError(Exception):
    """Erreur d'activation ou de validation de licence."""


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _post(endpoint: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        f"{LS_API}/{endpoint}",
        data=body,
        headers={
            "Accept":       "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
            msg = payload.get("error") or payload.get("message") or str(e)
        except Exception:
            msg = str(e)
        raise LicenseError(msg) from e
    except (urllib.error.URLError, socket.timeout) as e:
        raise LicenseError(f"Réseau indisponible : {e}") from e


# ── API publique licence ──────────────────────────────────────────────────────

def activate(license_key: str) -> dict:
    """Active la licence sur cette machine. À appeler une fois après achat."""
    key = (license_key or "").strip()
    if not key:
        raise LicenseError("Clé de licence vide")

    # Anti-PII : on n'envoie pas le hostname réel à Lemon Squeezy.
    node_hash = hashlib.sha256((platform.node() or "machine").encode()).hexdigest()[:12]
    instance_name = f"{node_hash} ({platform.system()})"
    data = _post("activate", {"license_key": key, "instance_name": instance_name})

    if not data.get("activated"):
        raise LicenseError(data.get("error") or "Activation refusée")

    instance = data.get("instance") or {}
    instance_id = instance.get("id")
    if not instance_id:
        raise LicenseError("Réponse LS sans instance_id")

    _save({
        "key":           key,
        "instance_id":   instance_id,
        "last_check":    _now_iso(),
        "status":        data.get("license_key", {}).get("status", "active"),
    })
    return data


# Issues possibles d'une vérification en ligne. On distingue soigneusement
# le rejet EXPLICITE de Lemon Squeezy (clé révoquée, remboursée…) du simple
# échec réseau : seul le premier est définitif, le second passe par la
# grâce hors-ligne de OFFLINE_GRACE_DAYS jours.
_OUTCOME_VALID    = "valid"
_OUTCOME_INVALID  = "invalid"        # réponse LS explicite : {"valid": false}
_OUTCOME_NETWORK  = "network_error"  # réseau KO / timeout → grâce offline
_OUTCOME_NO_LIC   = "no_license"


def _validate_remote() -> tuple[str, dict | None]:
    """Interroge Lemon Squeezy et retourne (issue, licence stockée).

    Rafraîchit last_check/status sur disque quand la réponse est valide.
    Ne supprime jamais rien : la décision (grâce, purge) revient aux appelants.
    """
    lic = _load()
    if not lic:
        return _OUTCOME_NO_LIC, None

    try:
        data = _post("validate", {
            "license_key": lic["key"],
            "instance_id": lic["instance_id"],
        })
    except LicenseError as e:
        logger.warning(f"validate: réseau KO ({e}) — fallback offline")
        return _OUTCOME_NETWORK, lic

    if data.get("valid"):
        lic["last_check"] = _now_iso()
        lic["status"]     = data.get("license_key", {}).get("status", "active")
        _save(lic)
        return _OUTCOME_VALID, lic

    logger.warning(f"Licence invalide : {data.get('error')}")
    return _OUTCOME_INVALID, lic


def validate(*, allow_offline: bool = True) -> bool:
    """Vérifie la licence stockée. Retourne True si valide."""
    outcome, lic = _validate_remote()
    if outcome == _OUTCOME_VALID:
        return True
    if outcome == _OUTCOME_NETWORK:
        return allow_offline and _offline_grace_ok(lic)
    # _OUTCOME_NO_LIC ou _OUTCOME_INVALID (rejet explicite)
    return False


def revalidate_if_due(max_age_hours: int = REVALIDATE_HOURS) -> bool:
    """Re-vérifie la licence en ligne si la dernière vérification est trop vieille.

    Pensée pour tourner en arrière-plan au démarrage (thread daemon) :
    - Pas de licence stockée → rien à faire (le gate s'en charge) → True.
    - last_check plus récent que max_age_hours → aucun appel réseau → True.
    - Rejet EXPLICITE de Lemon Squeezy (clé révoquée/remboursée) → la licence
      stockée est supprimée (_clear()) pour que le prochain lancement repasse
      par le gate licence/trial → False.
    - Échec réseau → grâce hors-ligne, la licence est CONSERVÉE.
    Ne lève jamais : toute erreur inattendue est loguée et ignorée.
    """
    try:
        lic = _load()
        if not lic:
            return True

        last = lic.get("last_check")
        if last:
            try:
                age_h = (datetime.now(timezone.utc)
                         - datetime.fromisoformat(last)).total_seconds() / 3600
                if age_h < max_age_hours:
                    return True  # vérification récente → pas d'appel réseau
            except ValueError:
                pass  # last_check illisible → on re-valide par prudence

        outcome, lic = _validate_remote()
        if outcome == _OUTCOME_INVALID:
            logger.warning("revalidate: rejet explicite Lemon Squeezy — purge de la licence")
            _clear()
            return False
        if outcome == _OUTCOME_NETWORK:
            return _offline_grace_ok(lic)  # licence conservée dans tous les cas
        return True  # _OUTCOME_VALID ou _OUTCOME_NO_LIC (course bénigne)
    except Exception as e:  # défensif : ne jamais faire planter le thread appelant
        logger.warning(f"revalidate_if_due: erreur inattendue ({e})")
        return True


def deactivate() -> bool:
    """Désactive cette machine (libère un siège). Appelé à la désinstallation."""
    lic = _load()
    if not lic:
        return True
    try:
        _post("deactivate", {
            "license_key": lic["key"],
            "instance_id": lic["instance_id"],
        })
    except LicenseError as e:
        logger.warning(f"deactivate échoué : {e}")
    _clear()
    return True


def is_activated() -> bool:
    """True s'il existe une licence stockée (n'appelle pas le réseau)."""
    return _load() is not None


def masked_key() -> str | None:
    """Clé de licence masquée pour affichage UI ("····-····-····-XXXX").

    Retourne None s'il n'y a pas de licence stockée. Évite que l'UI accède
    à la clé complète via l'API privée _load().
    """
    lic = _load()
    if not lic:
        return None
    key = str(lic.get("key") or "")
    if not key:
        return None
    return f"····-····-····-{key[-4:]}"


# ── API publique trial ────────────────────────────────────────────────────────

def has_trial() -> bool:
    """True si un fichier trial.json signé existe (valide ou non)."""
    return _load_trial() is not None


def start_trial() -> dict:
    """Démarre un trial de TRIAL_DAYS jours. Idempotent : ne réinitialise pas.

    Anti-reset : avant d'accorder 14 jours neufs, on consulte le marqueur
    redondant (TRIAL_MARKER_PATH). S'il est présent et signé correctement,
    on restaure trial.json avec les MÊMES dates — supprimer ou corrompre
    trial.json ne redonne donc pas un essai neuf. Si les DEUX emplacements
    ont été supprimés, un nouveau trial démarre (limitation assumée : sans
    serveur, aucune trace locale ne peut survivre à un effacement total).
    """
    existing = _load_trial()
    if existing is not None:
        return existing

    # trial.json absent ou altéré : le marqueur redondant fait foi s'il est valide.
    marker = _read_signed(TRIAL_MARKER_PATH)
    if marker is not None:
        logger.debug("trial: restauration depuis le marqueur redondant (pas de reset)")
        _save_trial(marker)
        return marker

    now = _now_iso()
    payload = {"started_at": now, "last_seen": now}
    _save_trial(payload)
    return payload


def trial_status() -> dict:
    """État du trial : {active, days_left, expired}. HMAC invalide → expired."""
    t = _load_trial()
    if t is None:
        return {"active": False, "days_left": 0, "expired": True}

    try:
        started = datetime.fromisoformat(t["started_at"])
        last_seen = datetime.fromisoformat(t.get("last_seen", t["started_at"]))
    except (KeyError, ValueError):
        return {"active": False, "days_left": 0, "expired": True}

    now = datetime.now(timezone.utc)

    # Anti-rollback : si l'horloge système recule sous last_seen, on refuse.
    if now < last_seen - timedelta(minutes=5):
        logger.warning("trial: rollback d'horloge détecté (now < last_seen)")
        return {"active": False, "days_left": 0, "expired": True}

    # Update last_seen si on a avancé dans le temps (réécrit le HMAC).
    if now > last_seen:
        t["last_seen"] = _now_iso()
        _save_trial(t)

    end = started + timedelta(days=TRIAL_DAYS)
    remaining = (end - now).total_seconds()
    days_left = max(0, int(remaining // 86400) + (1 if remaining > 0 else 0))
    if remaining <= 0:
        return {"active": False, "days_left": 0, "expired": True}
    return {"active": True, "days_left": days_left, "expired": False}


def is_licensed_or_trial_ok() -> bool:
    """True si une licence stockée OU un trial encore actif. Pas de réseau."""
    if is_activated():
        return True
    return trial_status()["active"]


# ── Persistance licence ───────────────────────────────────────────────────────

def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(LICENSE_PATH), exist_ok=True)
    tmp = LICENSE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, LICENSE_PATH)
    try:
        os.chmod(LICENSE_PATH, 0o600)
    except OSError:
        pass


def _load() -> dict | None:
    if not os.path.exists(LICENSE_PATH):
        return None
    try:
        with open(LICENSE_PATH) as f:
            d = json.load(f)
        if not isinstance(d, dict) or "key" not in d or "instance_id" not in d:
            return None
        return d
    except (json.JSONDecodeError, IOError):
        return None


def _clear() -> None:
    try:
        os.remove(LICENSE_PATH)
    except OSError:
        pass


# ── Persistance trial (HMAC) ──────────────────────────────────────────────────

def _trial_sign(payload: dict) -> str:
    """HMAC-SHA256 du payload canonicalisé (clés triées)."""
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_TRIAL_HMAC_KEY, msg, hashlib.sha256).hexdigest()


def _read_signed(path: str) -> dict | None:
    """Lit un fichier {payload, hmac} et vérifie la signature.

    Retourne le payload, ou None si absent/illisible/altéré. Ne lève jamais.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            record = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    sig = record.get("hmac")
    if not isinstance(payload, dict) or not isinstance(sig, str):
        return None
    if not hmac.compare_digest(_trial_sign(payload), sig):
        logger.warning(f"trial: HMAC invalide — {os.path.basename(path)} altéré")
        return None
    if "started_at" not in payload:
        return None
    return payload


def _save_marker(payload: dict) -> None:
    """Écrit le marqueur trial redondant. Silencieux : ne bloque jamais l'app."""
    try:
        marker_dir = os.path.dirname(TRIAL_MARKER_PATH)
        if marker_dir:
            os.makedirs(marker_dir, exist_ok=True)
        record = {"payload": payload, "hmac": _trial_sign(payload)}
        tmp = TRIAL_MARKER_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(record, f, separators=(",", ":"))
        os.replace(tmp, TRIAL_MARKER_PATH)
        try:
            os.chmod(TRIAL_MARKER_PATH, 0o600)
        except OSError:
            pass
    except OSError as e:
        logger.debug(f"trial: écriture du marqueur impossible ({e})")


def _save_trial(payload: dict) -> None:
    """Écrit ~/.voxaho/trial.json signé HMAC + le marqueur redondant."""
    os.makedirs(os.path.dirname(TRIAL_PATH), exist_ok=True)
    record = {"payload": payload, "hmac": _trial_sign(payload)}
    tmp = TRIAL_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(record, f, indent=2)
    os.replace(tmp, TRIAL_PATH)
    try:
        os.chmod(TRIAL_PATH, 0o600)
    except OSError:
        pass
    # Copie redondante hors ~/.voxaho : rend la suppression de trial.json inopérante.
    _save_marker(payload)


def _load_trial() -> dict | None:
    """Lit le trial signé, avec restauration croisée depuis le marqueur redondant.

    - trial.json absent mais marqueur valide → restaure trial.json (mêmes dates).
    - trial.json valide mais marqueur absent/altéré → recrée le marqueur.
    - trial.json présent mais altéré → None (invalide, pas de restauration ici ;
      start_trial() consultera le marqueur avant d'accorder un essai neuf).
    """
    if os.path.exists(TRIAL_PATH):
        payload = _read_signed(TRIAL_PATH)
        if payload is None:
            return None
        # Symétrique : si le marqueur a disparu (ou est altéré), on le recrée.
        if _read_signed(TRIAL_MARKER_PATH) is None:
            _save_marker(payload)
        return payload

    # trial.json supprimé : restauration silencieuse depuis le marqueur.
    marker = _read_signed(TRIAL_MARKER_PATH)
    if marker is not None:
        logger.debug("trial: trial.json absent — restauration depuis le marqueur")
        try:
            _save_trial(marker)  # réécrit trial.json avec les MÊMES dates
        except OSError as e:
            logger.warning(f"trial: restauration de trial.json impossible ({e})")
        return marker
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _offline_grace_ok(lic: dict) -> bool:
    last = lic.get("last_check")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    age_days = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400
    return age_days <= OFFLINE_GRACE_DAYS
