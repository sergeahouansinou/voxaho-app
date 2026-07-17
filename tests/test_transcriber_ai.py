"""
Tests de la COUCHE D'AIGUILLAGE du reformatage IA (`_reformat_text`).

Portée volontairement PURE : aucun vrai modèle Whisper n'est chargé (lazy load,
transcribe() jamais appelé) et aucun vrai LLM n'est requis (llama_cpp n'a PAS
besoin d'être installé). On isole la dépendance `core.llm` en injectant un faux
module dans `sys.modules`, puis on vérifie uniquement la logique de décision de
`Transcriber._reformat_text`.

On NE touche PAS à `_reformat` ni à `_is_hallucination` : leurs tests unitaires
existants restent la référence.
"""

import sys
import types
from pathlib import Path

# Rendre le package `core` importable quel que soit le mode d'invocation de pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.transcriber import Transcriber


# ── Faux module LLM injectable ────────────────────────────────────────────────

def _install_fake_llm(monkeypatch, *, available=True, ready=True,
                      reformat_return="Texte propre.", record=None):
    """Patche les fonctions du VRAI module `core.llm`.

    `core.llm` est toujours importable (ses imports internes — llama_cpp — sont
    protégés). On patche donc l'objet module que `from core import llm` résout
    réellement, plutôt que d'échanger l'entrée `sys.modules` : cette dernière est
    contournée dès que `core.llm` a déjà été importé par un autre test (le package
    `core` conserve alors un attribut `llm` qui court-circuite sys.modules), ce qui
    rendait ces tests dépendants de l'ordre d'exécution.

    `record` (liste optionnelle) reçoit les appels à reformat sous forme de
    tuples (text, language) → permet de vérifier que reformat n'est JAMAIS appelé
    dans les cas où l'IA doit être court-circuitée.
    """
    import core.llm as real

    def reformat(text, language="fr"):
        if record is not None:
            record.append((text, language))
        return reformat_return

    monkeypatch.setattr(real, "is_available", lambda: available)
    monkeypatch.setattr(real, "is_model_ready", lambda: ready)
    monkeypatch.setattr(real, "reformat", reformat)
    return real


@pytest.fixture
def transcriber():
    # reformatting=True par défaut ; le modèle Whisper n'est jamais chargé (lazy).
    return Transcriber(model="small", language="fr", reformatting=True)


# ── ai_reformat désactivé → règles, LLM jamais sollicité ─────────────────────

class TestAiDesactive:
    def test_ai_off_passe_par_les_regles(self, transcriber, monkeypatch):
        appels = []
        _install_fake_llm(monkeypatch, record=appels)
        transcriber.ai_reformat = False

        out = transcriber._reformat_text("euh bonjour", "fr")

        # Résultat identique au reformatage par règles
        assert out == transcriber._reformat("euh bonjour", "fr")
        assert out == "Bonjour."
        # llm.reformat ne doit JAMAIS avoir été appelé
        assert appels == []

    def test_ai_off_par_defaut(self):
        # Le nouvel attribut est bien False par défaut (n'invalide pas l'existant)
        t = Transcriber(model="small", language="fr")
        assert t.ai_reformat is False


# ── ai_reformat actif + LLM prêt → sortie LLM, règles NON appliquées ─────────

class TestAiActifSucces:
    def test_ai_utilise_la_sortie_llm(self, transcriber, monkeypatch):
        appels = []
        _install_fake_llm(
            monkeypatch, available=True, ready=True,
            reformat_return="Texte propre.", record=appels,
        )
        transcriber.ai_reformat = True

        out = transcriber._reformat_text("texte  brut euh", "fr")

        # La sortie est EXACTEMENT celle du LLM (les règles ne sont pas appliquées)
        assert out == "Texte propre."
        # Le LLM a bien reçu le texte et la langue
        assert appels == [("texte  brut euh", "fr")]


# ── ai_reformat actif mais échec → repli automatique sur les règles ──────────

class TestAiActifFallback:
    def test_reformat_none_repli_regles(self, transcriber, monkeypatch):
        _install_fake_llm(monkeypatch, reformat_return=None)
        transcriber.ai_reformat = True

        out = transcriber._reformat_text("euh bonjour", "fr")

        # Repli : on obtient EXACTEMENT le résultat des règles
        assert out == transcriber._reformat("euh bonjour", "fr")
        assert out == "Bonjour."

    def test_reformat_chaine_vide_repli_regles(self, transcriber, monkeypatch):
        _install_fake_llm(monkeypatch, reformat_return="   ")
        transcriber.ai_reformat = True

        out = transcriber._reformat_text("euh bonjour", "fr")
        assert out == transcriber._reformat("euh bonjour", "fr")
        assert out == "Bonjour."

    def test_reformat_exception_repli_regles(self, transcriber, monkeypatch):
        # Si reformat lève, on ne doit pas casser : repli sur les règles
        import core.llm as real
        monkeypatch.setattr(real, "is_available", lambda: True)
        monkeypatch.setattr(real, "is_model_ready", lambda: True)

        def boom(text, language="fr"):
            raise RuntimeError("échec LLM simulé")

        monkeypatch.setattr(real, "reformat", boom)
        transcriber.ai_reformat = True

        out = transcriber._reformat_text("euh bonjour", "fr")
        assert out == transcriber._reformat("euh bonjour", "fr")
        assert out == "Bonjour."


# ── ai_reformat actif mais LLM indisponible → règles, LLM jamais sollicité ───

class TestAiIndisponible:
    def test_is_available_false_passe_par_les_regles(self, transcriber, monkeypatch):
        appels = []
        _install_fake_llm(monkeypatch, available=False, ready=True, record=appels)
        transcriber.ai_reformat = True

        out = transcriber._reformat_text("euh bonjour", "fr")

        assert out == transcriber._reformat("euh bonjour", "fr")
        assert out == "Bonjour."
        # reformat ne doit pas être appelé quand is_available est False
        assert appels == []

    def test_modele_non_pret_passe_par_les_regles(self, transcriber, monkeypatch):
        appels = []
        _install_fake_llm(monkeypatch, available=True, ready=False, record=appels)
        transcriber.ai_reformat = True

        out = transcriber._reformat_text("euh bonjour", "fr")

        assert out == transcriber._reformat("euh bonjour", "fr")
        assert appels == []

    def test_module_llm_absent_passe_par_les_regles(self, transcriber, monkeypatch):
        # Simule l'absence totale du module core.llm : on retire l'attribut `llm`
        # du package `core` ET on neutralise l'entrée sys.modules, pour que
        # `from core import llm` lève réellement quel que soit l'ordre des tests
        # → comportement « IA indisponible » → repli sur les règles.
        import core as core_pkg
        monkeypatch.delattr(core_pkg, "llm", raising=False)
        monkeypatch.setitem(sys.modules, "core.llm", None)
        transcriber.ai_reformat = True

        out = transcriber._reformat_text("euh bonjour", "fr")
        assert out == transcriber._reformat("euh bonjour", "fr")
        assert out == "Bonjour."


# ── reformatting désactivé → texte brut inchangé quel que soit ai_reformat ───

class TestReformattingDesactive:
    def test_reformatting_off_texte_brut_ai_off(self, transcriber, monkeypatch):
        appels = []
        _install_fake_llm(monkeypatch, record=appels)
        transcriber.reformatting = False
        transcriber.ai_reformat = False

        out = transcriber._reformat_text("euh bonjour", "fr")
        assert out == "euh bonjour"  # strictement inchangé
        assert appels == []

    def test_reformatting_off_texte_brut_ai_on(self, transcriber, monkeypatch):
        # Même IA activée, reformatting=False court-circuite tout : texte brut
        appels = []
        _install_fake_llm(monkeypatch, record=appels)
        transcriber.reformatting = False
        transcriber.ai_reformat = True

        out = transcriber._reformat_text("euh bonjour", "fr")
        assert out == "euh bonjour"
        # Le LLM ne doit pas être sollicité quand le reformatage est désactivé
        assert appels == []


# ── update_settings : maj à chaud de ai_reformat ─────────────────────────────

class TestUpdateSettings:
    def test_update_settings_active_ai(self):
        t = Transcriber(model="small", language="fr")
        assert t.ai_reformat is False
        t.update_settings(ai_reformat=True)
        assert t.ai_reformat is True

    def test_update_settings_none_ne_touche_pas_ai(self):
        t = Transcriber(model="small", language="fr", ai_reformat=True)
        t.update_settings(ai_reformat=None)  # ne modifie rien
        assert t.ai_reformat is True

    def test_update_settings_desactive_ai(self):
        t = Transcriber(model="small", language="fr", ai_reformat=True)
        t.update_settings(ai_reformat=False)
        assert t.ai_reformat is False
