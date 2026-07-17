"""
Tests du module core.license : trial durci (marqueur redondant),
re-validation périodique (revalidate_if_due) et masked_key.

Isolation totale :
  - LICENSE_PATH / TRIAL_PATH / TRIAL_MARKER_PATH sont redirigés vers tmp_path.
  - _post est monkeypatché : AUCUN test ne touche le réseau. Par défaut, tout
    appel réseau non prévu fait échouer le test (AssertionError).

Lancement : ./venv/bin/python -m pytest tests/test_license.py -v
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from core import license as lic


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Redirige tous les chemins vers tmp_path et interdit le réseau par défaut."""
    monkeypatch.setattr(lic, "LICENSE_PATH", str(tmp_path / "voxaho" / "license.json"))
    monkeypatch.setattr(lic, "TRIAL_PATH", str(tmp_path / "voxaho" / "trial.json"))
    # Le marqueur vit volontairement dans un AUTRE dossier (comme en prod).
    monkeypatch.setattr(lic, "TRIAL_MARKER_PATH", str(tmp_path / "meta" / ".voxaho_meta"))

    def _no_network(endpoint, data):
        raise AssertionError(f"appel réseau inattendu : {endpoint}")

    monkeypatch.setattr(lic, "_post", _no_network)
    yield


@pytest.fixture
def post_recorder(monkeypatch):
    """Remplace _post par un enregistreur configurable (réponse ou exception)."""
    calls = []
    state = {"response": None, "exception": None}

    def _fake_post(endpoint, data):
        calls.append((endpoint, data))
        if state["exception"] is not None:
            raise state["exception"]
        return state["response"]

    monkeypatch.setattr(lic, "_post", _fake_post)
    return calls, state


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso_ago(**kwargs) -> str:
    """ISO UTC dans le passé (ex. _iso_ago(hours=100))."""
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


def _iso_in(**kwargs) -> str:
    """ISO UTC dans le futur (ex. _iso_in(days=1))."""
    return (datetime.now(timezone.utc) + timedelta(**kwargs)).isoformat()


def _write_license(last_check: str | None = None, key: str = "ABCD-EFGH-IJKL-MNOP") -> dict:
    data = {"key": key, "instance_id": "inst-1", "status": "active"}
    if last_check is not None:
        data["last_check"] = last_check
    lic._save(data)
    return data


# ── Trial : cycle nominal ─────────────────────────────────────────────────────

class TestTrialNominal:
    def test_start_trial_donne_14_jours_actifs(self):
        payload = lic.start_trial()
        assert "started_at" in payload

        status = lic.trial_status()
        assert status["active"] is True
        assert status["expired"] is False
        assert status["days_left"] == lic.TRIAL_DAYS

    def test_start_trial_idempotent(self):
        first = lic.start_trial()
        second = lic.start_trial()
        assert second["started_at"] == first["started_at"]

    def test_save_trial_ecrit_le_marqueur(self):
        lic.start_trial()
        assert os.path.exists(lic.TRIAL_PATH)
        assert os.path.exists(lic.TRIAL_MARKER_PATH)
        # Même payload signé dans les deux emplacements.
        with open(lic.TRIAL_MARKER_PATH) as f:
            marker = json.load(f)
        with open(lic.TRIAL_PATH) as f:
            trial = json.load(f)
        assert marker["payload"]["started_at"] == trial["payload"]["started_at"]
        assert marker["hmac"] == trial["hmac"]


# ── Trial : durcissement anti-suppression ────────────────────────────────────

class TestTrialAntiReset:
    def test_suppression_trial_json_restaure_depuis_marqueur_load(self):
        original = lic.start_trial()
        os.remove(lic.TRIAL_PATH)

        restored = lic._load_trial()
        assert restored is not None
        assert restored["started_at"] == original["started_at"]  # pas de reset
        assert os.path.exists(lic.TRIAL_PATH)  # fichier recréé

    def test_suppression_trial_json_start_trial_ne_reset_pas(self):
        # Trial vieux de 10 jours : après suppression, il doit rester vieux de 10 jours.
        old = _iso_ago(days=10)
        lic._save_trial({"started_at": old, "last_seen": _iso_ago(minutes=1)})
        os.remove(lic.TRIAL_PATH)

        payload = lic.start_trial()
        assert payload["started_at"] == old
        status = lic.trial_status()
        assert status["active"] is True
        assert status["days_left"] == lic.TRIAL_DAYS - 10  # 4 jours, pas 14

    def test_trial_json_corrompu_start_trial_restaure_depuis_marqueur(self):
        # Corrompre trial.json (au lieu de le supprimer) ne donne pas non plus
        # un essai neuf : start_trial repart du marqueur.
        original = lic.start_trial()
        with open(lic.TRIAL_PATH) as f:
            record = json.load(f)
        record["payload"]["started_at"] = _iso_in(days=1)  # HMAC désormais faux
        with open(lic.TRIAL_PATH, "w") as f:
            json.dump(record, f)

        assert lic._load_trial() is None  # fichier altéré → invalide
        payload = lic.start_trial()
        assert payload["started_at"] == original["started_at"]

    def test_marqueur_supprime_recree_depuis_trial_json(self):
        # Symétrique : trial.json valide mais marqueur disparu → recréation.
        lic.start_trial()
        os.remove(lic.TRIAL_MARKER_PATH)

        payload = lic._load_trial()
        assert payload is not None
        assert os.path.exists(lic.TRIAL_MARKER_PATH)

    def test_suppression_des_deux_fichiers_donne_un_nouveau_trial(self):
        # COMPORTEMENT ASSUMÉ : sans serveur, aucune trace locale ne survit à
        # un effacement des DEUX emplacements → un nouvel essai démarre.
        old = lic.start_trial()
        os.remove(lic.TRIAL_PATH)
        os.remove(lic.TRIAL_MARKER_PATH)

        assert lic.has_trial() is False
        fresh = lic.start_trial()
        assert fresh["started_at"] != old["started_at"]
        assert lic.trial_status()["active"] is True


# ── Trial : intégrité et horloge ──────────────────────────────────────────────

class TestTrialIntegrite:
    def test_rollback_horloge_expire_le_trial(self):
        # last_seen signé dans le futur = l'horloge système a reculé → refus.
        lic._save_trial({
            "started_at": _iso_ago(days=1),
            "last_seen": _iso_in(days=1),
        })
        status = lic.trial_status()
        assert status["active"] is False
        assert status["expired"] is True
        assert status["days_left"] == 0

    def test_trial_expire_apres_14_jours(self):
        lic._save_trial({
            "started_at": _iso_ago(days=lic.TRIAL_DAYS + 1),
            "last_seen": _iso_ago(minutes=1),
        })
        status = lic.trial_status()
        assert status["active"] is False
        assert status["expired"] is True

    def test_hmac_altere_rend_le_trial_invalide(self):
        lic.start_trial()
        # Altérer le payload sans recalculer le HMAC (édition naïve).
        with open(lic.TRIAL_PATH) as f:
            record = json.load(f)
        record["payload"]["started_at"] = _iso_in(days=365)
        with open(lic.TRIAL_PATH, "w") as f:
            json.dump(record, f)

        assert lic._load_trial() is None

    def test_marqueur_altere_ignore(self):
        # trial.json supprimé + marqueur altéré → aucune restauration possible.
        lic.start_trial()
        os.remove(lic.TRIAL_PATH)
        with open(lic.TRIAL_MARKER_PATH) as f:
            record = json.load(f)
        record["payload"]["started_at"] = _iso_ago(days=1)
        with open(lic.TRIAL_MARKER_PATH, "w") as f:
            json.dump(record, f)

        assert lic._load_trial() is None


# ── revalidate_if_due ─────────────────────────────────────────────────────────

class TestRevalidateIfDue:
    def test_sans_licence_aucun_appel_reseau(self, post_recorder):
        calls, _ = post_recorder
        assert lic.revalidate_if_due() is True
        assert calls == []

    def test_last_check_recent_aucun_appel_reseau(self, post_recorder):
        calls, _ = post_recorder
        _write_license(last_check=_iso_ago(hours=1))

        assert lic.revalidate_if_due(max_age_hours=72) is True
        assert calls == []  # pas d'appel réseau
        assert lic.is_activated() is True

    def test_last_check_vieux_reponse_invalide_purge_la_licence(self, post_recorder):
        calls, state = post_recorder
        state["response"] = {"valid": False, "error": "license key revoked"}
        _write_license(last_check=_iso_ago(hours=100))

        assert lic.revalidate_if_due(max_age_hours=72) is False
        assert len(calls) == 1  # un seul appel validate
        assert lic.is_activated() is False  # licence supprimée
        assert not os.path.exists(lic.LICENSE_PATH)

    def test_last_check_vieux_erreur_reseau_conserve_la_licence(self, post_recorder):
        calls, state = post_recorder
        state["exception"] = lic.LicenseError("Réseau indisponible")
        # 100 h = ~4,2 jours : encore dans la grâce offline de 7 jours.
        _write_license(last_check=_iso_ago(hours=100))

        assert lic.revalidate_if_due(max_age_hours=72) is True  # grâce offline
        assert len(calls) == 1
        assert lic.is_activated() is True  # licence CONSERVÉE

    def test_erreur_reseau_grace_depassee_conserve_quand_meme(self, post_recorder):
        # Au-delà de la grâce offline : False, mais on ne purge PAS (ce n'est
        # pas un rejet explicite — le réseau peut revenir).
        _, state = post_recorder
        state["exception"] = lic.LicenseError("Réseau indisponible")
        _write_license(last_check=_iso_ago(days=10))

        assert lic.revalidate_if_due(max_age_hours=72) is False
        assert lic.is_activated() is True

    def test_last_check_vieux_reponse_valide_rafraichit_last_check(self, post_recorder):
        calls, state = post_recorder
        state["response"] = {"valid": True, "license_key": {"status": "active"}}
        _write_license(last_check=_iso_ago(hours=100))

        assert lic.revalidate_if_due(max_age_hours=72) is True
        assert len(calls) == 1
        stored = lic._load()
        age = datetime.now(timezone.utc) - datetime.fromisoformat(stored["last_check"])
        assert age < timedelta(minutes=1)  # last_check rafraîchi

    def test_last_check_absent_declenche_la_verification(self, post_recorder):
        calls, state = post_recorder
        state["response"] = {"valid": True, "license_key": {"status": "active"}}
        _write_license(last_check=None)

        assert lic.revalidate_if_due() is True
        assert len(calls) == 1


# ── validate : séparation rejet explicite / échec réseau ─────────────────────

class TestValidate:
    def test_reponse_invalide_retourne_false_sans_purger(self, post_recorder):
        # validate() ne supprime jamais rien : la purge est la responsabilité
        # de revalidate_if_due (rejet explicite uniquement).
        _, state = post_recorder
        state["response"] = {"valid": False, "error": "refunded"}
        _write_license(last_check=_iso_ago(hours=1))

        assert lic.validate() is False
        assert lic.is_activated() is True

    def test_erreur_reseau_grace_offline_ok(self, post_recorder):
        _, state = post_recorder
        state["exception"] = lic.LicenseError("timeout")
        _write_license(last_check=_iso_ago(days=2))  # < 7 jours

        assert lic.validate(allow_offline=True) is True

    def test_erreur_reseau_grace_offline_depassee(self, post_recorder):
        _, state = post_recorder
        state["exception"] = lic.LicenseError("timeout")
        _write_license(last_check=_iso_ago(days=10))  # > 7 jours

        assert lic.validate(allow_offline=True) is False

    def test_erreur_reseau_offline_interdit(self, post_recorder):
        _, state = post_recorder
        state["exception"] = lic.LicenseError("timeout")
        _write_license(last_check=_iso_ago(hours=1))

        assert lic.validate(allow_offline=False) is False

    def test_sans_licence_false_sans_reseau(self, post_recorder):
        calls, _ = post_recorder
        assert lic.validate() is False
        assert calls == []


# ── masked_key ────────────────────────────────────────────────────────────────

class TestMaskedKey:
    def test_avec_licence_masque_tout_sauf_les_4_derniers(self):
        _write_license(key="ABCD-EFGH-IJKL-MNOP")
        assert lic.masked_key() == "····-····-····-MNOP"

    def test_sans_licence_retourne_none(self):
        assert lic.masked_key() is None

    def test_cle_vide_retourne_none(self):
        # Licence dégénérée (clé vide) : on ne retourne pas de masque trompeur.
        lic._save({"key": "", "instance_id": "inst-1"})
        assert lic.masked_key() is None
