"""
Tests Phase 0 « Vitesse foudroyante » du Transcriber.

Portée volontairement PURE : on ne charge JAMAIS un vrai modèle Whisper et on ne
transcrit JAMAIS de vrai audio. On teste :
- select_backend() : logique déterministe de choix du backend (tous les cas) ;
- beam_size : stockage + valeur par défaut (1) ;
- preload() : ne lève jamais, même si le chargement du modèle échoue.
"""

import sys
from pathlib import Path

# Rendre le package `core` importable quel que soit le mode d'invocation de pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

from core.transcriber import Transcriber, select_backend


# ── select_backend : logique de sélection déterministe ───────────────────────

class TestSelectBackend:
    def test_auto_darwin_arm64_avec_mlx_donne_mlx(self):
        assert select_backend(
            "auto", is_darwin=True, is_arm64=True, mlx_available=True
        ) == "mlx"

    def test_auto_darwin_arm64_sans_mlx_donne_cpu(self):
        # Cas de CE Mac de test : arm64 mais mlx_whisper absent → fallback CPU
        assert select_backend(
            "auto", is_darwin=True, is_arm64=True, mlx_available=False
        ) == "cpu"

    def test_auto_darwin_intel_donne_cpu(self):
        assert select_backend(
            "auto", is_darwin=True, is_arm64=False, mlx_available=True
        ) == "cpu"

    def test_auto_linux_donne_cpu(self):
        assert select_backend(
            "auto", is_darwin=False, is_arm64=False, mlx_available=False
        ) == "cpu"
        # Même avec mlx improbablement dispo, hors macOS on reste CPU
        assert select_backend(
            "auto", is_darwin=False, is_arm64=True, mlx_available=True
        ) == "cpu"

    def test_requested_cpu_force_toujours_cpu(self):
        # Même sur une plateforme MLX-capable, "cpu" force le CPU
        assert select_backend(
            "cpu", is_darwin=True, is_arm64=True, mlx_available=True
        ) == "cpu"

    def test_requested_mlx_sans_dispo_fallback_cpu(self):
        assert select_backend(
            "mlx", is_darwin=True, is_arm64=True, mlx_available=False
        ) == "cpu"

    def test_requested_mlx_avec_dispo_donne_mlx(self):
        assert select_backend(
            "mlx", is_darwin=True, is_arm64=True, mlx_available=True
        ) == "mlx"

    def test_valeur_inconnue_traitee_comme_auto(self):
        # Valeur non reconnue → comportement sûr (identique à "auto")
        assert select_backend(
            "bogus", is_darwin=False, is_arm64=False, mlx_available=False
        ) == "cpu"


# ── beam_size : stockage + valeur par défaut ─────────────────────────────────

class TestBeamSize:
    def test_beam_size_defaut_est_1(self):
        t = Transcriber(model="small", language="fr")
        assert t.beam_size == 1

    def test_beam_size_stocke(self):
        t = Transcriber(model="small", language="fr", beam_size=5)
        assert t.beam_size == 5

    def test_update_settings_modifie_beam_size(self):
        t = Transcriber(model="small", language="fr", beam_size=1)
        t.update_settings(beam_size=3)
        assert t.beam_size == 3

    def test_update_settings_none_ne_modifie_pas(self):
        t = Transcriber(model="small", language="fr", beam_size=2, reformatting=True)
        t.update_settings(beam_size=None)  # ne touche à rien
        assert t.beam_size == 2
        assert t.reformatting is True

    def test_update_settings_language_auto_devient_none(self):
        t = Transcriber(model="small", language="fr")
        t.update_settings(language="auto")
        assert t.language is None

    def test_update_settings_reformatting(self):
        t = Transcriber(model="small", language="fr", reformatting=True)
        t.update_settings(reformatting=False)
        assert t.reformatting is False


# ── Backend effectif résolu au constructeur ──────────────────────────────────

class TestBackendResolution:
    def test_backend_cpu_force(self):
        t = Transcriber(model="small", language="fr", backend="cpu")
        assert t._backend == "cpu"

    def test_backend_par_defaut_ne_casse_jamais(self):
        # Sur ce Mac (arm64 sans mlx) le backend résolu doit être "cpu"
        t = Transcriber(model="small", language="fr")  # backend="auto"
        assert t._backend in ("cpu", "mlx")


# ── preload() : robustesse (ne lève jamais) ──────────────────────────────────

class TestPreload:
    def test_preload_ne_leve_pas_si_get_model_echoue(self, monkeypatch):
        # On force le backend CPU pour garantir le passage par _get_model,
        # puis on fait échouer le chargement du modèle.
        t = Transcriber(model="small", language="fr", backend="cpu")

        def boom():
            raise RuntimeError("échec simulé du chargement du modèle")

        monkeypatch.setattr(t, "_get_model", boom)

        # Ne doit PAS lever (l'échec est loggé, le modèle sera rechargé plus tard)
        t.preload()
        # Le modèle n'a pas pu être chargé
        assert t._model is None

    def test_preload_idempotent_ne_leve_pas(self, monkeypatch):
        t = Transcriber(model="small", language="fr", backend="cpu")
        monkeypatch.setattr(t, "_get_model", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        # Appels répétés : toujours silencieux
        t.preload()
        t.preload()

    def test_transcribe_audio_vide_retourne_chaine_vide(self):
        t = Transcriber(model="small", language="fr", backend="cpu")
        # len == 0 → court-circuit avant tout chargement de modèle
        assert t.transcribe(np.zeros(0, dtype=np.float32)) == ""
        assert t.transcribe(None) == ""
