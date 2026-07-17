"""
Tests du module core.llm : wrapper LLM local optionnel (reformatage IA, Phase 2B).

Isolation totale — AUCUN vrai modèle, AUCUN téléchargement, AUCUN llama_cpp requis :
  - _import_llama est monkeypatché pour simuler la présence/absence du module ;
  - MODEL_PATH est redirigé vers tmp_path pour is_model_ready() ;
  - hf_hub_download est monkeypatché pour download_model() (aucun réseau) ;
  - le cache d'instance Llama module-level est réinitialisé avant/après chaque test.

Lancement : ./venv/bin/python -m pytest tests/test_llm.py -v
"""

import os

import pytest

from core import llm


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_cache():
    """Réinitialise le cache d'instance Llama avant ET après chaque test."""
    llm.unload()
    yield
    llm.unload()


@pytest.fixture
def install_llama(monkeypatch, tmp_path):
    """Installe un faux modèle « prêt » (fichier sur disque + classe Llama mockée).

    Retourne une fonction install(content) -> calls : elle configure le texte
    renvoyé par create_chat_completion et enregistre les appels
    (init + chat completion) pour vérification. Aucun llama_cpp réel n'est importé.
    """
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"x")  # taille > 0 → is_model_ready() True
    monkeypatch.setattr(llm, "MODEL_PATH", str(model_file))

    calls = []

    def _install(content):
        class _FakeLlama:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def create_chat_completion(self, **kwargs):
                calls.append(("chat", kwargs))
                return {"choices": [{"message": {"content": content}}]}

        monkeypatch.setattr(llm, "_import_llama", lambda: _FakeLlama)
        return calls

    return _install


# ── is_available ────────────────────────────────────────────────────────────────

class TestIsAvailable:
    def test_module_present_retourne_true(self, monkeypatch):
        monkeypatch.setattr(llm, "_import_llama", lambda: object)
        assert llm.is_available() is True

    def test_module_absent_retourne_false(self, monkeypatch):
        monkeypatch.setattr(llm, "_import_llama", lambda: None)
        assert llm.is_available() is False


# ── is_model_ready ──────────────────────────────────────────────────────────────

class TestIsModelReady:
    def test_fichier_absent_retourne_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(llm, "MODEL_PATH", str(tmp_path / "absent.gguf"))
        assert llm.is_model_ready() is False

    def test_fichier_vide_retourne_false(self, monkeypatch, tmp_path):
        empty = tmp_path / "empty.gguf"
        empty.write_bytes(b"")  # taille 0 → non prêt
        monkeypatch.setattr(llm, "MODEL_PATH", str(empty))
        assert llm.is_model_ready() is False

    def test_fichier_present_retourne_true(self, monkeypatch, tmp_path):
        model = tmp_path / "model.gguf"
        model.write_bytes(b"gguf-data")
        monkeypatch.setattr(llm, "MODEL_PATH", str(model))
        assert llm.is_model_ready() is True


# ── reformat ────────────────────────────────────────────────────────────────────

class TestReformat:
    def test_indisponible_retourne_none(self, monkeypatch):
        # llama_cpp absent → aucune reformulation possible → fallback (None).
        monkeypatch.setattr(llm, "_import_llama", lambda: None)
        assert llm.reformat("un texte quelconque") is None

    def test_modele_absent_retourne_none(self, monkeypatch, tmp_path):
        # llama_cpp présent MAIS modèle GGUF non téléchargé → None.
        monkeypatch.setattr(llm, "_import_llama", lambda: object)
        monkeypatch.setattr(llm, "MODEL_PATH", str(tmp_path / "absent.gguf"))
        assert llm.reformat("un texte quelconque") is None

    def test_texte_vide_retourne_chaine_vide(self, monkeypatch):
        # Cas vide traité AVANT tout accès au modèle : "" même sans IA.
        monkeypatch.setattr(llm, "_import_llama", lambda: None)
        assert llm.reformat("") == ""
        assert llm.reformat("   ") == ""
        assert llm.reformat(None) == ""

    def test_sortie_nettoyee_attendue(self, install_llama):
        install_llama("Bonjour, comment ça va ?")
        assert llm.reformat("bonjour euh comment ca va") == "Bonjour, comment ça va ?"

    def test_retire_prefixe_parasite_et_guillemets(self, install_llama):
        install_llama('Voici le texte : "Bonjour le monde."')
        assert llm.reformat("bonjour le monde") == "Bonjour le monde."

    def test_retire_guillemets_typographiques(self, install_llama):
        install_llama("« Ceci est un test. »")
        assert llm.reformat("ceci est un test") == "Ceci est un test."

    def test_garde_fou_sortie_trop_longue_retourne_none(self, install_llama):
        # Entrée courte ("ok", 2 car.) et sortie > 3× plus longue → hallucination.
        install_llama("x" * 100)
        assert llm.reformat("ok") is None

    def test_garde_fou_sortie_vide_retourne_none(self, install_llama):
        install_llama("   ")  # après strip → vide → aberrant
        assert llm.reformat("bonjour") is None

    def test_parametres_transmis_au_modele(self, install_llama):
        calls = install_llama("Salut.")
        llm.reformat("salut", language="en")

        chat_calls = [kw for kind, kw in calls if kind == "chat"]
        assert len(chat_calls) == 1
        kwargs = chat_calls[0]
        assert kwargs["temperature"] == 0.2
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == llm._SYSTEM_PROMPTS["en"]
        assert messages[1] == {"role": "user", "content": "salut"}

    def test_langue_inconnue_utilise_le_prompt_francais(self, install_llama):
        calls = install_llama("Salut.")
        llm.reformat("salut", language="xx")

        kwargs = [kw for kind, kw in calls if kind == "chat"][0]
        assert kwargs["messages"][0]["content"] == llm._SYSTEM_PROMPTS["fr"]

    def test_instance_mise_en_cache_entre_deux_appels(self, install_llama):
        calls = install_llama("Ok.")
        llm.reformat("premier")
        llm.reformat("second")

        inits = [c for c in calls if c[0] == "init"]
        assert len(inits) == 1  # modèle chargé une seule fois (cache)

    def test_erreur_du_modele_retourne_none(self, monkeypatch, tmp_path):
        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"x")
        monkeypatch.setattr(llm, "MODEL_PATH", str(model_file))

        class _BoomLlama:
            def __init__(self, **kwargs):
                pass

            def create_chat_completion(self, **kwargs):
                raise RuntimeError("inference crash")

        monkeypatch.setattr(llm, "_import_llama", lambda: _BoomLlama)
        assert llm.reformat("bonjour") is None


# ── unload ──────────────────────────────────────────────────────────────────────

class TestUnload:
    def test_libere_le_cache(self, install_llama):
        calls = install_llama("Ok.")
        llm.reformat("bonjour")
        assert llm._llm is not None

        llm.unload()
        assert llm._llm is None

        # Après unload, un nouvel appel recharge le modèle (2e init).
        llm.reformat("re-bonjour")
        inits = [c for c in calls if c[0] == "init"]
        assert len(inits) == 2


# ── download_model ────────────────────────────────────────────────────────────

class TestDownloadModel:
    def test_succes_ecrit_le_fichier_et_appelle_le_callback(self, monkeypatch, tmp_path):
        import huggingface_hub

        models_dir = tmp_path / "models"
        monkeypatch.setattr(llm, "MODELS_DIR", str(models_dir))
        monkeypatch.setattr(llm, "MODEL_PATH", str(models_dir / llm.MODEL_FILENAME))

        def _fake_download(repo_id, filename, local_dir):
            assert repo_id == llm.MODEL_REPO
            assert filename == llm.MODEL_FILENAME
            os.makedirs(local_dir, exist_ok=True)
            dest = os.path.join(local_dir, filename)
            with open(dest, "wb") as f:
                f.write(b"gguf")
            return dest

        monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_download)

        fractions = []
        assert llm.download_model(progress_cb=fractions.append) is True
        assert llm.is_model_ready() is True
        assert fractions[0] == 0.0 and fractions[-1] == 1.0

    def test_echec_reseau_retourne_false(self, monkeypatch, tmp_path):
        import huggingface_hub

        monkeypatch.setattr(llm, "MODELS_DIR", str(tmp_path / "models"))
        monkeypatch.setattr(llm, "MODEL_PATH", str(tmp_path / "models" / llm.MODEL_FILENAME))

        def _boom(repo_id, filename, local_dir):
            raise OSError("réseau indisponible")

        monkeypatch.setattr(huggingface_hub, "hf_hub_download", _boom)
        assert llm.download_model() is False

    def test_callback_qui_leve_est_ignore(self, monkeypatch, tmp_path):
        import huggingface_hub

        models_dir = tmp_path / "models"
        monkeypatch.setattr(llm, "MODELS_DIR", str(models_dir))
        monkeypatch.setattr(llm, "MODEL_PATH", str(models_dir / llm.MODEL_FILENAME))

        def _fake_download(repo_id, filename, local_dir):
            os.makedirs(local_dir, exist_ok=True)
            dest = os.path.join(local_dir, filename)
            with open(dest, "wb") as f:
                f.write(b"gguf")
            return dest

        monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_download)

        def _bad_cb(_fraction):
            raise ValueError("callback cassé")

        # Le callback défaillant ne doit pas faire échouer le téléchargement.
        assert llm.download_model(progress_cb=_bad_cb) is True
