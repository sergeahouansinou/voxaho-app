"""
Tests de core.profiles (Phase 3b « Profils par application »).

Trois volets :
  1. CRUD des profils (base SQLite neuve isolée dans tmp_path) ;
  2. resolve_for_app — résolution du profil pour un nom d'app donné (défensif) ;
  3. effective_settings — fonction PURE : défauts de la config surchargés par les
     champs NON-None du profil (profil None → base inchangée).

Isolation : base neuve via core.db.set_db_path(). La table `profiles` est créée
PARESSEUSEMENT par le module (aucune modif de core.db.ensure_schema).

Lancer : ./venv/bin/python -m pytest tests/test_profiles.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import db, profiles


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.set_db_path(str(tmp_path / "t.db"))
    yield
    db.set_db_path(db.DB_PATH)


# ── CRUD ─────────────────────────────────────────────────────────────────────

class TestCrud:

    def test_add_minimal_et_list(self):
        pid = profiles.add_profile(app_pattern="Code")
        assert pid > 0
        listed = profiles.list_profiles()
        assert len(listed) == 1
        p = listed[0]
        assert p["id"] == pid
        assert p["app_pattern"] == "Code"
        assert p["name"] is None
        # Colonnes de réglage non fournies → None (« garder le défaut »).
        for key in ("language", "model", "reformatting", "ai_reformat", "translate_to"):
            assert p[key] is None
        assert p["created_at"]

    def test_add_avec_surcharges(self):
        pid = profiles.add_profile(
            name="Dev anglais", app_pattern="Code",
            language="en", model="small",
            reformatting=False, ai_reformat=True, translate_to="fr",
        )
        p = profiles.list_profiles()[0]
        assert p["id"] == pid
        assert p["name"] == "Dev anglais"
        assert p["language"] == "en"
        assert p["model"] == "small"
        # reformatting/ai_reformat stockés en INTEGER, ré-hydratés en bool.
        assert p["reformatting"] is False
        assert p["ai_reformat"] is True
        assert p["translate_to"] == "fr"

    def test_add_trim_pattern_et_name(self):
        profiles.add_profile(name="  Mail perso  ", app_pattern="  Mail  ")
        p = profiles.list_profiles()[0]
        assert p["app_pattern"] == "Mail"
        assert p["name"] == "Mail perso"

    def test_add_pattern_vide_retourne_moins_un(self):
        assert profiles.add_profile(app_pattern="   ") == -1
        assert profiles.list_profiles() == []

    def test_bool_false_distinct_de_none(self):
        """False (forcer désactivé) doit rester distinct de None (défaut)."""
        pid = profiles.add_profile(app_pattern="X", reformatting=False)
        p = profiles.list_profiles()[0]
        assert p["reformatting"] is False   # pas None
        assert pid > 0

    def test_update_partiel(self):
        pid = profiles.add_profile(app_pattern="Code", language="en")
        profiles.update_profile(pid, language="de", model="medium")
        p = profiles.list_profiles()[0]
        assert p["language"] == "de"
        assert p["model"] == "medium"
        assert p["app_pattern"] == "Code"   # inchangé

    def test_update_remet_a_none(self):
        """Passer explicitement None repasse le champ en défaut."""
        pid = profiles.add_profile(app_pattern="Code", language="en", reformatting=True)
        profiles.update_profile(pid, language=None, reformatting=None)
        p = profiles.list_profiles()[0]
        assert p["language"] is None
        assert p["reformatting"] is None

    def test_update_pattern_vide_ignore(self):
        """Un motif vide fourni à update est ignoré (motif obligatoire)."""
        pid = profiles.add_profile(app_pattern="Code")
        profiles.update_profile(pid, app_pattern="   ")
        assert profiles.list_profiles()[0]["app_pattern"] == "Code"

    def test_update_sans_champ_ne_fait_rien(self):
        pid = profiles.add_profile(app_pattern="Code", language="en")
        profiles.update_profile(pid)  # aucun champ → no-op
        assert profiles.list_profiles()[0]["language"] == "en"

    def test_update_cle_inconnue_ignoree(self):
        pid = profiles.add_profile(app_pattern="Code")
        profiles.update_profile(pid, inexistant="valeur")  # ignoré
        assert profiles.list_profiles()[0]["app_pattern"] == "Code"

    def test_remove(self):
        p1 = profiles.add_profile(app_pattern="Code")
        profiles.add_profile(app_pattern="Mail")
        profiles.remove_profile(p1)
        patterns = [p["app_pattern"] for p in profiles.list_profiles()]
        assert patterns == ["Mail"]

    def test_list_ordre_creation(self):
        profiles.add_profile(app_pattern="Code")
        profiles.add_profile(app_pattern="Mail")
        profiles.add_profile(app_pattern="Chrome")
        patterns = [p["app_pattern"] for p in profiles.list_profiles()]
        assert patterns == ["Code", "Mail", "Chrome"]


# ── resolve_for_app ──────────────────────────────────────────────────────────

class TestResolveForApp:

    def test_resolution_par_nom(self):
        profiles.add_profile(app_pattern="Code", language="en")
        profiles.add_profile(app_pattern="Mail", language="fr")
        prof = profiles.resolve_for_app("Visual Studio Code")
        assert prof is not None and prof["app_pattern"] == "Code"

    def test_aucun_profil_correspondant(self):
        profiles.add_profile(app_pattern="Code")
        assert profiles.resolve_for_app("Safari") is None

    def test_app_none(self):
        profiles.add_profile(app_pattern="Code")
        assert profiles.resolve_for_app(None) is None

    def test_base_vide(self):
        assert profiles.resolve_for_app("Code") is None

    def test_utilise_active_app_monkeypatche(self, monkeypatch):
        """Injection ROBUSTE À L'ORDRE : on patche l'attribut du VRAI module
        appcontext (pas sys.modules). resolve_for_app délègue le matching à
        appcontext.match_profile via le module → un patch de match_profile est vu.
        """
        from core import appcontext
        profiles.add_profile(app_pattern="Code", language="en")
        # On force match_profile à toujours renvoyer None : resolve doit suivre.
        monkeypatch.setattr(appcontext, "match_profile", lambda app, profs: None)
        assert profiles.resolve_for_app("Visual Studio Code") is None


# ── effective_settings (pur) ─────────────────────────────────────────────────

class TestEffectiveSettings:

    BASE = {
        "language": "fr", "model": "small", "reformatting": True,
        "ai_reformat": False, "translate_to": None,
    }

    def test_profil_none_retourne_base(self):
        eff = profiles.effective_settings(self.BASE, None)
        assert eff == {
            "language": "fr", "model": "small", "reformatting": True,
            "ai_reformat": False, "translate_to": None,
        }

    def test_profil_none_ne_mute_pas_la_base(self):
        eff = profiles.effective_settings(self.BASE, None)
        eff["language"] = "en"
        assert self.BASE["language"] == "fr"   # base intacte

    def test_surcharge_partielle(self):
        prof = {"language": "en", "ai_reformat": True}
        eff = profiles.effective_settings(self.BASE, prof)
        assert eff["language"] == "en"        # surchargé
        assert eff["ai_reformat"] is True     # surchargé
        assert eff["model"] == "small"        # défaut conservé
        assert eff["reformatting"] is True    # défaut conservé
        assert eff["translate_to"] is None    # défaut conservé

    def test_champs_none_ignores(self):
        """Un profil dont tous les champs de réglage sont None ⇒ base inchangée."""
        prof = {
            "language": None, "model": None, "reformatting": None,
            "ai_reformat": None, "translate_to": None,
        }
        eff = profiles.effective_settings(self.BASE, prof)
        assert eff == dict(self.BASE)

    def test_surcharge_desactive_reformatage(self):
        """Un profil peut FORCER reformatting=False par-dessus un défaut True."""
        prof = {"reformatting": False}
        eff = profiles.effective_settings(self.BASE, prof)
        assert eff["reformatting"] is False

    def test_surcharge_traduction(self):
        prof = {"translate_to": "en"}
        eff = profiles.effective_settings(self.BASE, prof)
        assert eff["translate_to"] == "en"

    def test_bool_stocke_en_int_converti(self):
        """Si le profil vient de la base (int 0/1), la sortie est bien un bool."""
        prof = {"reformatting": 1, "ai_reformat": 0}
        eff = profiles.effective_settings(self.BASE, prof)
        assert eff["reformatting"] is True
        assert eff["ai_reformat"] is False

    def test_base_incomplete_defauts_none(self):
        """Une base sans certaines clés donne des effectifs à None pour celles-ci."""
        eff = profiles.effective_settings({}, None)
        assert eff == {
            "language": None, "model": None, "reformatting": None,
            "ai_reformat": None, "translate_to": None,
        }

    def test_integration_resolve_puis_effective(self):
        """Bout-en-bout : add → resolve → effective."""
        profiles.add_profile(app_pattern="Code", language="en", ai_reformat=True)
        prof = profiles.resolve_for_app("Visual Studio Code")
        eff = profiles.effective_settings(self.BASE, prof)
        assert eff["language"] == "en"
        assert eff["ai_reformat"] is True
        assert eff["model"] == "small"   # défaut conservé
