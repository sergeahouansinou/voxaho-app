"""
Tests de la traduction à la volée locale (Phase 3).

Isolation TOTALE — AUCUN vrai modèle, AUCUN téléchargement, AUCUN llama_cpp requis :
  - l'accès au LLM est mocké (soit `translator._get_llm` directement, soit
    `core.llm._get_llm` / `translator._import_llama` pour exercer les deux chemins
    d'accès réels) ;
  - le cache d'instance Llama locale est réinitialisé avant/après chaque test.

Injection ROBUSTE À L'ORDRE des tests : on patche les attributs du VRAI objet
module (`monkeypatch.setattr(module, ...)`) plutôt que d'échanger `sys.modules`,
car `from core import X` court-circuite sys.modules dès que le package `core` a
déjà l'attribut (leçon des tests de reformatage).

Lancement : ./venv/bin/python -m pytest tests/test_translator.py -v
"""

import sys
from pathlib import Path

# Rendre le package `core` importable quel que soit le mode d'invocation de pytest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import translator
from core.transcriber import Transcriber


# ── Faux instance Llama ───────────────────────────────────────────────────────

class _FakeLlama:
    """Fausse instance Llama : renvoie un contenu figé et enregistre les appels."""

    def __init__(self, content, record=None):
        self._content = content
        self._record = record

    def create_chat_completion(self, **kwargs):
        if self._record is not None:
            self._record.append(kwargs)
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture(autouse=True)
def reset_cache():
    """Réinitialise le cache d'instance Llama locale avant ET après chaque test."""
    translator.unload()
    yield
    translator.unload()


def _use_fake_llm(monkeypatch, content, record=None):
    """Court-circuite l'accès LLM : translator._get_llm() → fausse instance."""
    fake = _FakeLlama(content, record=record)
    monkeypatch.setattr(translator, "_get_llm", lambda: fake)
    return fake


# ── is_available ────────────────────────────────────────────────────────────────

class TestIsAvailable:
    def test_true_si_llm_dispo_et_pret(self, monkeypatch):
        import core.llm as real_llm
        monkeypatch.setattr(real_llm, "is_available", lambda: True)
        monkeypatch.setattr(real_llm, "is_model_ready", lambda: True)
        assert translator.is_available() is True

    def test_false_si_llm_indispo(self, monkeypatch):
        import core.llm as real_llm
        monkeypatch.setattr(real_llm, "is_available", lambda: False)
        monkeypatch.setattr(real_llm, "is_model_ready", lambda: True)
        assert translator.is_available() is False

    def test_false_si_modele_non_pret(self, monkeypatch):
        import core.llm as real_llm
        monkeypatch.setattr(real_llm, "is_available", lambda: True)
        monkeypatch.setattr(real_llm, "is_model_ready", lambda: False)
        assert translator.is_available() is False

    def test_false_si_module_llm_absent(self, monkeypatch):
        # core.llm totalement absent → import défensif → False.
        import core as core_pkg
        monkeypatch.delattr(core_pkg, "llm", raising=False)
        monkeypatch.setitem(sys.modules, "core.llm", None)
        assert translator.is_available() is False


# ── LANGUAGE_NAMES ────────────────────────────────────────────────────────────

class TestLanguageNames:
    def test_couvre_les_langues_de_base(self):
        for code in ("fr", "en", "es", "de", "it", "pt", "nl", "zh", "ja", "ar", "ru"):
            assert code in translator.LANGUAGE_NAMES
            assert isinstance(translator.LANGUAGE_NAMES[code], str)

    def test_noms_en_anglais(self):
        assert translator.LANGUAGE_NAMES["fr"] == "French"
        assert translator.LANGUAGE_NAMES["en"] == "English"


# ── translate : cas dégradés ──────────────────────────────────────────────────

class TestTranslateDegrade:
    def test_texte_vide_retourne_chaine_vide(self, monkeypatch):
        # Court-circuité AVANT tout accès au LLM.
        monkeypatch.setattr(translator, "_get_llm", lambda: pytest.fail("ne doit pas être appelé"))
        assert translator.translate("", "en") == ""
        assert translator.translate("   ", "en") == ""
        assert translator.translate(None, "en") == ""

    def test_cible_absente_retourne_none(self, monkeypatch):
        monkeypatch.setattr(translator, "_get_llm", lambda: pytest.fail("ne doit pas être appelé"))
        assert translator.translate("bonjour", "") is None
        assert translator.translate("bonjour", None) is None

    def test_cible_inconnue_retourne_none(self, monkeypatch):
        monkeypatch.setattr(translator, "_get_llm", lambda: pytest.fail("ne doit pas être appelé"))
        assert translator.translate("bonjour", "xx") is None

    def test_llm_indisponible_retourne_none(self, monkeypatch):
        monkeypatch.setattr(translator, "_get_llm", lambda: None)
        assert translator.translate("bonjour", "en") is None


# ── translate : cas nominal ───────────────────────────────────────────────────

class TestTranslateNominal:
    def test_traduction_simple(self, monkeypatch):
        _use_fake_llm(monkeypatch, "Hello, how are you?")
        assert translator.translate("Bonjour, comment ça va ?", "en") == "Hello, how are you?"

    def test_prompt_cible_transmis(self, monkeypatch):
        appels = []
        _use_fake_llm(monkeypatch, "Hello.", record=appels)
        translator.translate("Bonjour.", "en")

        assert len(appels) == 1
        kwargs = appels[0]
        assert kwargs["temperature"] == 0.2
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "English" in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": "Bonjour."}

    def test_source_connue_incluse_dans_le_prompt(self, monkeypatch):
        appels = []
        _use_fake_llm(monkeypatch, "Hello.", record=appels)
        translator.translate("Bonjour.", "en", source_lang="fr")

        system = appels[0]["messages"][0]["content"]
        assert "from French" in system
        assert "into English" in system

    def test_source_inconnue_ignoree(self, monkeypatch):
        appels = []
        _use_fake_llm(monkeypatch, "Hello.", record=appels)
        translator.translate("Bonjour.", "en", source_lang="xx")

        system = appels[0]["messages"][0]["content"]
        assert "from" not in system  # source inconnue → non mentionnée
        assert "into English" in system

    def test_nettoie_prefixe_et_guillemets(self, monkeypatch):
        _use_fake_llm(monkeypatch, 'Here is the translation: "Hello world."')
        assert translator.translate("Bonjour le monde.", "en") == "Hello world."

    def test_nettoie_guillemets_typographiques(self, monkeypatch):
        _use_fake_llm(monkeypatch, "« Hello. »")
        assert translator.translate("Bonjour.", "en") == "Hello."


# ── translate : garde-fous anti-hallucination ─────────────────────────────────

class TestTranslateGardeFous:
    def test_sortie_trop_longue_retourne_none(self, monkeypatch):
        # Entrée courte ("ok", 2 car.) et sortie > 3× plus longue → None.
        _use_fake_llm(monkeypatch, "x" * 100)
        assert translator.translate("ok", "en") is None

    def test_sortie_vide_retourne_none(self, monkeypatch):
        _use_fake_llm(monkeypatch, "   ")  # après strip → vide
        assert translator.translate("bonjour", "en") is None

    def test_exception_du_modele_retourne_none(self, monkeypatch):
        class _BoomLlama:
            def create_chat_completion(self, **kwargs):
                raise RuntimeError("inference crash")

        monkeypatch.setattr(translator, "_get_llm", lambda: _BoomLlama())
        assert translator.translate("bonjour", "en") is None


# ── _get_llm : deux chemins d'accès réels ─────────────────────────────────────

class TestGetLlm:
    def test_reutilise_instance_partagee_de_core_llm(self, monkeypatch):
        # Chemin 1 : core.llm._get_llm() fournit une instance → réutilisée telle quelle.
        import core.llm as real_llm
        fake = _FakeLlama("X")
        monkeypatch.setattr(real_llm, "_get_llm", lambda: fake)
        assert translator._get_llm() is fake

    def test_fallback_charge_instance_propre(self, monkeypatch, tmp_path):
        # Chemin 2 : instance partagée indisponible → on charge NOTRE instance
        # depuis core.llm.MODEL_PATH via translator._import_llama (mocké).
        import core.llm as real_llm
        monkeypatch.setattr(real_llm, "_get_llm", lambda: None)

        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"x")  # taille > 0
        monkeypatch.setattr(real_llm, "MODEL_PATH", str(model_file))

        inits = []

        class _FakeLlamaCls:
            def __init__(self, **kwargs):
                inits.append(kwargs)

            def create_chat_completion(self, **kwargs):
                return {"choices": [{"message": {"content": "Hello."}}]}

        monkeypatch.setattr(translator, "_import_llama", lambda: _FakeLlamaCls)

        inst = translator._get_llm()
        assert inst is not None
        # Chargé depuis le bon chemin, une seule fois (cache).
        assert inits and inits[0]["model_path"] == str(model_file)
        assert translator._get_llm() is inst  # cache : même instance
        assert len(inits) == 1

    def test_fallback_none_si_llama_absent(self, monkeypatch):
        import core.llm as real_llm
        monkeypatch.setattr(real_llm, "_get_llm", lambda: None)
        monkeypatch.setattr(translator, "_import_llama", lambda: None)
        assert translator._get_llm() is None


# ── unload ──────────────────────────────────────────────────────────────────────

class TestUnload:
    def test_libere_instance_propre(self, monkeypatch, tmp_path):
        import core.llm as real_llm
        monkeypatch.setattr(real_llm, "_get_llm", lambda: None)

        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"x")
        monkeypatch.setattr(real_llm, "MODEL_PATH", str(model_file))

        inits = []

        class _FakeLlamaCls:
            def __init__(self, **kwargs):
                inits.append(kwargs)

            def create_chat_completion(self, **kwargs):
                return {"choices": [{"message": {"content": "Hello."}}]}

        monkeypatch.setattr(translator, "_import_llama", lambda: _FakeLlamaCls)

        translator._get_llm()
        assert translator._own_llm is not None
        translator.unload()
        assert translator._own_llm is None

        # Après unload, un nouvel accès recharge (2e init).
        translator._get_llm()
        assert len(inits) == 2


# ══════════════════════════════════════════════════════════════════════════════
#  Intégration transcriber : aiguillage de la traduction
# ══════════════════════════════════════════════════════════════════════════════

def _install_fake_translator(monkeypatch, *, translate_return="Hello.", record=None):
    """Patche `translate` sur le VRAI module core.translator (robuste à l'ordre)."""
    import core.translator as real

    def _translate(text, target_lang, source_lang=None):
        if record is not None:
            record.append((text, target_lang, source_lang))
        return translate_return

    monkeypatch.setattr(real, "translate", _translate)
    return real


class TestTranscriberConstruction:
    def test_translate_to_none_par_defaut(self):
        # Le nouvel attribut vaut None par défaut (n'invalide pas l'existant).
        t = Transcriber(model="small", language="fr")
        assert t.translate_to is None

    def test_translate_to_positionnel_dernier(self):
        # Ajouté en DERNIER param → utilisable en kwarg sans casser l'existant.
        t = Transcriber(model="small", language="fr", translate_to="en")
        assert t.translate_to == "en"


class TestTranscriberUpdateSettings:
    def test_sentinelle_ne_touche_pas(self):
        t = Transcriber(model="small", language="fr", translate_to="en")
        t.update_settings()  # translate_to omis → inchangé
        assert t.translate_to == "en"
        t.update_settings(beam_size=3)  # autre param → translate_to toujours inchangé
        assert t.translate_to == "en"

    def test_maj_vers_une_cible(self):
        t = Transcriber(model="small", language="fr")
        t.update_settings(translate_to="es")
        assert t.translate_to == "es"

    def test_maj_vers_none_desactive(self):
        # None est une valeur VALIDE (désactiver) → distinguée de « omis » via _UNSET.
        t = Transcriber(model="small", language="fr", translate_to="en")
        t.update_settings(translate_to=None)
        assert t.translate_to is None


class TestTranscriberTranslateText:
    """Unit test de la méthode d'aiguillage, sans charger Whisper."""

    def test_sortie_non_vide_utilisee(self, monkeypatch):
        _install_fake_translator(monkeypatch, translate_return="Hello.")
        t = Transcriber(model="small", language="fr", translate_to="en")
        assert t._translate_text("Bonjour.", "fr") == "Hello."

    def test_source_transmise(self, monkeypatch):
        appels = []
        _install_fake_translator(monkeypatch, translate_return="Hello.", record=appels)
        t = Transcriber(model="small", language="fr", translate_to="en")
        t._translate_text("Bonjour.", "fr")
        assert appels == [("Bonjour.", "en", "fr")]

    def test_none_garde_original(self, monkeypatch):
        _install_fake_translator(monkeypatch, translate_return=None)
        t = Transcriber(model="small", language="fr", translate_to="en")
        assert t._translate_text("Bonjour.", "fr") == "Bonjour."

    def test_vide_garde_original(self, monkeypatch):
        _install_fake_translator(monkeypatch, translate_return="   ")
        t = Transcriber(model="small", language="fr", translate_to="en")
        assert t._translate_text("Bonjour.", "fr") == "Bonjour."

    def test_module_absent_garde_original(self, monkeypatch):
        import core as core_pkg
        monkeypatch.delattr(core_pkg, "translator", raising=False)
        monkeypatch.setitem(sys.modules, "core.translator", None)
        t = Transcriber(model="small", language="fr", translate_to="en")
        assert t._translate_text("Bonjour.", "fr") == "Bonjour."


class TestTranscribeAiguillage:
    """Aiguillage complet dans transcribe(), backend mocké (aucun vrai Whisper)."""

    def _make(self, monkeypatch, translate_to):
        # postprocess/reformatting désactivés → le texte du backend passe tel quel
        # jusqu'à l'étape de traduction, qu'on isole.
        t = Transcriber(model="small", language="fr", reformatting=False,
                        postprocess=False, translate_to=translate_to)
        monkeypatch.setattr(
            t, "_transcribe_cpu",
            lambda audio, initial_prompt=None: ([("bonjour", 0.0, 0.0)], "fr"),
        )
        return t

    def test_translate_to_none_aucun_appel(self, monkeypatch):
        import numpy as np
        appels = []
        _install_fake_translator(monkeypatch, translate_return="hello", record=appels)
        t = self._make(monkeypatch, translate_to=None)

        out = t.transcribe(np.zeros(10, dtype=np.float32))
        assert out == "bonjour"       # inchangé
        assert appels == []           # translate() JAMAIS appelé

    def test_translate_to_vide_aucun_appel(self, monkeypatch):
        import numpy as np
        appels = []
        _install_fake_translator(monkeypatch, translate_return="hello", record=appels)
        t = self._make(monkeypatch, translate_to="")

        out = t.transcribe(np.zeros(10, dtype=np.float32))
        assert out == "bonjour"
        assert appels == []

    def test_translate_to_actif_traduit(self, monkeypatch):
        import numpy as np
        appels = []
        _install_fake_translator(monkeypatch, translate_return="hello", record=appels)
        t = self._make(monkeypatch, translate_to="en")

        out = t.transcribe(np.zeros(10, dtype=np.float32))
        assert out == "hello"
        # texte final propre + cible + source (langue forcée "fr")
        assert appels == [("bonjour", "en", "fr")]

    def test_translate_to_actif_repli_si_none(self, monkeypatch):
        import numpy as np
        _install_fake_translator(monkeypatch, translate_return=None)
        t = self._make(monkeypatch, translate_to="en")

        out = t.transcribe(np.zeros(10, dtype=np.float32))
        assert out == "bonjour"  # repli silencieux sur l'original
