"""
Transcription locale via faster-whisper (ou MLX sur Apple Silicon) + reformatage.
Le modèle est chargé en lazy (premier appel) ou explicitement via preload(),
et protégé par un lock pour éviter les race conditions lors des changements
de modèle à chaud.

Phase 0 « Vitesse foudroyante » :
- L'audio (np.ndarray float32 mono 16 kHz) est passé DIRECTEMENT au moteur —
  plus aucun fichier WAV temporaire (gain 50-200 ms).
- beam_size configurable (défaut 1 = greedy, le plus rapide).
- preload() charge le modèle + warmup pour rendre la 1ʳᵉ dictée quasi instantanée.
- Backend MLX optionnel (Apple Silicon) avec fallback CPU garanti.
"""

import re
import logging
import platform
import importlib.util
import threading
import numpy as np

logger = logging.getLogger(__name__)

# Sons d'hésitation par langue. UNIQUEMENT des sons purs, jamais de vrais mots :
# tout terme pouvant être un mot légitime de la langue (ex. "like" en anglais,
# "quoi"/"ben" en français, "um" en allemand, "eh"/"este" en espagnol) est
# volontairement exclu pour ne jamais détruire une vraie dictée.
# Langue absente du dict → aucune suppression de fillers.
FILLERS_BY_LANG = {
    "fr": ["euh", "heu", "hum", "hem"],
    "en": ["uh", "um", "erm", "uhm", "mm-hmm"],
    "es": ["em", "ehm"],
    "de": ["äh", "ähm", "öhm", "hm"],
    "it": ["ehm", "uhm", "mah"],
}

# Phrases connues que Whisper hallucine sur du silence ou du bruit
# (crédits de sous-titres, appels à s'abonner, remerciements de fin de vidéo).
# Elles sont comparées après normalisation (minuscules, sans ponctuation,
# espaces réduits) et ne filtrent un segment que si le motif couvre
# la quasi-totalité du segment — voir _is_hallucination().
HALLUCINATION_PATTERNS = [
    # Français
    "sous-titres réalisés par la communauté d'amara.org",
    "sous-titres réalisés par la communauté",
    "sous-titres réalisés par soustitreur.com",
    "sous-titrage société radio-canada",
    "sous-titrage st' 501",
    "merci d'avoir regardé cette vidéo",
    "merci d'avoir regardé la vidéo",
    "merci d'avoir regardé",
    "n'hésitez pas à vous abonner",
    "abonnez-vous à la chaîne",
    "abonnez-vous",
    # Anglais
    "subtitles by the amara.org community",
    "subs by www.zeoranger.co.uk",
    "subtitles by",
    "thank you for watching",
    "thanks for watching",
    "please subscribe to my channel",
    "don't forget to like and subscribe",
    "subscribe to the channel",
    "please subscribe",
    "subscribe",
    # Espagnol
    "subtítulos realizados por la comunidad de amara.org",
    "subtítulos por",
    "gracias por ver el vídeo",
    "gracias por ver",
    "suscríbete al canal",
    "suscríbete",
    # Allemand
    "untertitelung aufgrund der amara.org-community",
    "untertitel der amara.org-community",
    "untertitel im auftrag des zdf für funk",
    "untertitel im auftrag des zdf",
    "untertitel von stephanie geiges",
    "vielen dank fürs zuschauen",
    "danke fürs zuschauen",
    # Italien
    "sottotitoli creati dalla comunità amara.org",
    "sottotitoli e revisione a cura di qtss",
    "sottotitoli a cura di",
    "grazie per aver guardato il video",
    "grazie per aver guardato",
    "iscriviti al canale",
    # Génériques
    "amara.org",
    "www.mooji.org",
    "www.",
]


def _normalize_for_blocklist(text: str) -> str:
    """Normalise un texte pour comparaison avec la blocklist :
    minuscules, ponctuation remplacée par des espaces, espaces réduits."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


_HALLUCINATION_PATTERNS_NORM = sorted(
    {_normalize_for_blocklist(p) for p in HALLUCINATION_PATTERNS},
    key=len, reverse=True,
)


def _is_hallucination(text: str) -> bool:
    """Retourne True si le segment ENTIER correspond à une hallucination connue.

    Un motif ne déclenche le filtrage que s'il couvre au moins 80 % du segment
    normalisé : une vraie phrase dictée qui ne fait que CITER le motif
    (ex. « va sur amara.org pour voir les sous-titres ») est donc conservée,
    tandis qu'un segment isolé « Sous-titres réalisés par la communauté
    d'Amara.org » est supprimé.
    """
    norm = _normalize_for_blocklist(text)
    if not norm:
        return False
    for pattern in _HALLUCINATION_PATTERNS_NORM:
        if pattern in norm and len(pattern) >= 0.8 * len(norm):
            return True
    return False


# ── Sélection du backend de calcul ───────────────────────────────────────────

# Mapping nom de modèle → repo Hugging Face MLX pour le chemin Apple Silicon.
# NOTE : ces repos sont « à valider sur Apple Silicon avec mlx-whisper installé » ;
# ce Mac de test n'a pas mlx_whisper, le chemin MLX n'est donc pas exercé ici.
MLX_MODEL_REPOS = {
    "tiny":            "mlx-community/whisper-tiny-mlx",
    "base":            "mlx-community/whisper-base-mlx",
    "small":           "mlx-community/whisper-small-mlx",
    "medium":          "mlx-community/whisper-medium-mlx",
    "large-v3":        "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo":  "mlx-community/whisper-large-v3-turbo",
    "distil-large-v3": "mlx-community/distil-whisper-large-v3",
}


def _mlx_whisper_available() -> bool:
    """True si le module `mlx_whisper` est importable (sans l'importer réellement)."""
    try:
        return importlib.util.find_spec("mlx_whisper") is not None
    except Exception:
        return False


def select_backend(requested: str, *, is_darwin: bool, is_arm64: bool,
                   mlx_available: bool) -> str:
    """Choisit le backend effectif, de façon PURE et déterministe (sans effet de bord).

    Retourne "mlx" ou "cpu".
    - "cpu"  : force toujours faster-whisper CPU (chemin garanti).
    - "mlx"  : force MLX si disponible, sinon fallback "cpu".
    - "auto" : MLX seulement si macOS + arm64 + module mlx_whisper présent ;
               sinon "cpu" (fallback garanti, ne casse jamais).
    """
    if requested == "cpu":
        return "cpu"
    if requested == "mlx":
        return "mlx" if mlx_available else "cpu"
    # requested == "auto" (ou toute valeur inconnue → comportement sûr par défaut)
    if is_darwin and is_arm64 and mlx_available:
        return "mlx"
    return "cpu"


class Transcriber:
    def __init__(self, model: str = "small", language: str = "fr",
                 reformatting: bool = True, beam_size: int = 1,
                 backend: str = "auto", postprocess: bool = True):
        self.model_name   = model
        self.language     = None if language == "auto" else language
        self.reformatting = reformatting
        self.beam_size    = beam_size
        self.backend_requested = backend
        # Post-traitement « intelligence locale » : amorce Whisper depuis le
        # dictionnaire perso + correction des termes + expansion des snippets.
        # Activé par défaut ; se désactive pour retrouver le comportement brut.
        self.postprocess  = postprocess
        self._model       = None
        self._model_lock  = threading.Lock()

        # Résolution du backend effectif (déterministe). Sur ce Mac (arm64 sans
        # mlx_whisper) → "cpu" : le chemin faster-whisper reste 100 % fonctionnel.
        self._backend = select_backend(
            backend,
            is_darwin=(platform.system() == "Darwin"),
            is_arm64=(platform.machine() in ("arm64", "aarch64")),
            mlx_available=_mlx_whisper_available(),
        )
        if backend == "mlx" and self._backend != "mlx":
            logger.warning(
                "Backend MLX demandé mais mlx_whisper introuvable → fallback CPU (faster-whisper)."
            )

    # ── Chargement du modèle ──────────────────────────────────────────────────

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise RuntimeError(
                    "faster-whisper n'est pas installé. Lancez : pip install faster-whisper"
                )
            self._model = WhisperModel(
                self.model_name,
                device="cpu",
                compute_type="int8",
            )
        return self._model

    def unload_model(self):
        """Libère la RAM occupée par le modèle (utile à la fermeture)."""
        with self._model_lock:
            self._model = None

    def update_model(self, model_name: str):
        """Change de modèle à chaud, thread-safe."""
        with self._model_lock:
            if model_name != self.model_name:
                self.model_name = model_name
                self._model     = None

    def update_settings(self, beam_size: int | None = None,
                        language: str | None = None,
                        reformatting: bool | None = None):
        """Met à jour à chaud beam_size / language / reformatting (thread-safe).

        Les paramètres laissés à None ne sont pas modifiés. Ne recharge PAS le
        modèle (contrairement à update_model) : ces réglages sont lus à chaque
        transcription. Les attributs restent aussi modifiables directement.
        """
        with self._model_lock:
            if beam_size is not None:
                self.beam_size = beam_size
            if language is not None:
                self.language = None if language == "auto" else language
            if reformatting is not None:
                self.reformatting = reformatting

    def preload(self):
        """Charge le modèle ET fait un warmup (transcrit ~0.5 s de silence) pour
        forcer l'allocation mémoire et le JIT : la 1ʳᵉ dictée réelle devient quasi
        instantanée.

        Thread-safe (passe par transcribe() qui réutilise self._model_lock),
        idempotent (le modèle est mis en cache), et ne LÈVE JAMAIS : tout échec
        (dépendance absente, modèle introuvable…) est simplement loggé, le modèle
        sera rechargé au 1er usage réel. Peut donc tourner dans un thread daemon.
        """
        try:
            warmup_audio = np.zeros(8000, dtype=np.float32)  # 0,5 s de silence @ 16 kHz
            # Même chemin que transcribe() → charge réellement le modèle du backend
            # choisi et déclenche le warmup (allocation + JIT).
            self.transcribe(warmup_audio)
            logger.info("preload(): modèle '%s' chargé et réchauffé (backend=%s).",
                        self.model_name, self._backend)
        except Exception as e:
            logger.warning(f"preload() a échoué (le modèle sera chargé au 1er usage): {e}")

    # ── Transcription ─────────────────────────────────────────────────────────

    def transcribe(self, audio_array: np.ndarray) -> str:
        if audio_array is None or len(audio_array) == 0:
            return ""

        # Numpy direct : faster-whisper ET mlx_whisper acceptent un np.ndarray
        # float32 mono 16 kHz — plus aucun fichier WAV temporaire (gain 50-200 ms).
        audio = np.ascontiguousarray(audio_array, dtype=np.float32)

        # Amorce Whisper issue du dictionnaire personnel (ou None). Défensif :
        # tout échec du module dico → None → comportement d'origine intact.
        initial_prompt = self._dictionary_prompt()

        # Chaque backend renvoie une liste de tuples (texte, no_speech_prob,
        # avg_logprob) + la langue détectée. Le filtrage et le reformatage qui
        # suivent sont STRICTEMENT communs aux deux backends (comportement inchangé).
        if self._backend == "mlx":
            try:
                raw_segments, detected_lang = self._transcribe_mlx(audio)
            except Exception as e:
                # Fallback garanti : le chemin CPU ne doit jamais casser.
                logger.warning(f"Backend MLX indisponible à l'exécution → fallback CPU: {e}")
                self._backend = "cpu"
                raw_segments, detected_lang = self._transcribe_cpu(audio, initial_prompt)
        else:
            raw_segments, detected_lang = self._transcribe_cpu(audio, initial_prompt)

        # Filtrer les hallucinations de Whisper générées sur silence/bruit :
        # segments quasi muets peu fiables + phrases fantômes de la blocklist.
        textes = []
        for seg_text, no_speech_prob, avg_logprob in raw_segments:
            seg_text = seg_text.strip()
            if not seg_text:
                continue
            if no_speech_prob > 0.6 and avg_logprob < -0.8:
                continue  # probablement du silence mal interprété
            if _is_hallucination(seg_text):
                continue  # phrase fantôme connue (ex. crédits de sous-titres)
            textes.append(seg_text)
        text = " ".join(textes).strip()

        detected_lang = detected_lang if self.language is None else self.language
        if self.reformatting:
            text = self._reformat(text, detected_lang)

        # Post-traitement « intelligence locale », EN AVAL du reformatage et
        # uniquement si activé : correction des termes du dico puis expansion des
        # snippets. Défensif (voir _apply_postprocessing).
        if self.postprocess and text:
            text = self._apply_postprocessing(text)

        return text

    # ── Intelligence locale (dictionnaire + snippets) ────────────────────────────

    def _dictionary_prompt(self):
        """Amorce Whisper issue du dictionnaire personnel, ou None.

        Import différé + défensif : si le module dictionnaire est indisponible
        (absent, base illisible, prompt vide…), retourne None → `initial_prompt`
        n'est pas transmis et le comportement d'origine est conservé. N'agit que
        si le post-traitement est activé (self.postprocess).
        """
        if not self.postprocess:
            return None
        try:
            from core import dictionary
            prompt = dictionary.whisper_prompt()
            return prompt or None
        except Exception as e:  # pragma: no cover - purement défensif
            logger.debug("whisper_prompt indisponible, initial_prompt ignoré: %s", e)
            return None

    def _apply_postprocessing(self, text: str) -> str:
        """Applique dictionary.correct_text puis snippets.expand_text, dans cet ordre.

        Chaque brique est isolée (import différé + try/except) : l'échec d'un
        module d'intelligence n'affecte jamais la dictée — on renvoie toujours le
        meilleur texte obtenu jusque-là.
        """
        try:
            from core import dictionary
            text = dictionary.correct_text(text)
        except Exception as e:  # pragma: no cover - purement défensif
            logger.debug("correct_text ignoré: %s", e)
        try:
            from core import snippets
            text = snippets.expand_text(text)
        except Exception as e:  # pragma: no cover - purement défensif
            logger.debug("expand_text ignoré: %s", e)
        return text

    # ── Backends ────────────────────────────────────────────────────────────────

    def _transcribe_cpu(self, audio: np.ndarray, initial_prompt: str | None = None):
        """Chemin faster-whisper CPU int8 (garanti). Renvoie (segments_bruts, langue).

        `initial_prompt` (amorce issue du dictionnaire perso) est transmis tel
        quel à faster-whisper ; None = aucune amorce (comportement d'origine).
        """
        with self._model_lock:
            # Capturer les références locales SOUS le lock. Si update_model()/
            # update_settings() modifie l'état pendant la transcription, on garde
            # le modèle courant vivant jusqu'à la fin de l'appel.
            model     = self._get_model()
            language  = self.language
            beam_size = self.beam_size

        segments, info = model.transcribe(
            audio,
            language=language,
            beam_size=beam_size,
            initial_prompt=initial_prompt,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        raw = [
            (
                seg.text,
                getattr(seg, "no_speech_prob", 0.0),
                getattr(seg, "avg_logprob", 0.0),
            )
            for seg in segments
        ]
        return raw, info.language

    def _transcribe_mlx(self, audio: np.ndarray):
        """Chemin MLX (Apple Silicon). Renvoie (segments_bruts, langue).

        NOTE : « à valider sur Apple Silicon avec mlx-whisper installé ». mlx_whisper
        n'est pas une dépendance dure (import protégé) ; absent, on ne passe jamais ici.
        """
        import mlx_whisper  # import protégé : jamais requis si backend != mlx

        repo = MLX_MODEL_REPOS.get(
            self.model_name, f"mlx-community/whisper-{self.model_name}-mlx"
        )
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=repo,
            language=self.language,
            # beam_size n'est pas exposé de la même façon par mlx_whisper : on
            # conserve ses défauts (décodage greedy), cohérent avec la cible vitesse.
        )
        raw = [
            (
                seg.get("text", ""),
                seg.get("no_speech_prob", 0.0),
                seg.get("avg_logprob", 0.0),
            )
            for seg in result.get("segments", [])
        ]
        return raw, result.get("language", self.language)

    # ── Reformatage ───────────────────────────────────────────────────────────

    def _reformat(self, text: str, lang: str) -> str:
        if not text:
            return text

        # Préserver les URLs avant nettoyage
        url_pattern = r"https?://\S+"
        urls = re.findall(url_pattern, text)
        placeholder = "__URL{i}__"
        for i, url in enumerate(urls):
            text = text.replace(url, placeholder.format(i=i), 1)

        # Supprimer les sons d'hésitation propres à la langue détectée.
        # Langue inconnue ou non listée → aucune suppression de fillers.
        fillers = FILLERS_BY_LANG.get(lang or "", [])
        for filler in sorted(fillers, key=len, reverse=True):  # long → court pour éviter chevauchement
            text = re.sub(
                r"(?<!\w)" + re.escape(filler) + r"(?!\w)",
                " ",
                text,
                flags=re.IGNORECASE,
            )

        # Nettoyage des espaces et ponctuation
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s([,.!?:;])", r"\1", text)
        text = re.sub(r"([.!?])\s*[.!?]+", r"\1", text)  # double ponctuation

        # Majuscule début de phrase (Unicode : gère aussi ä, ö, ü, ñ, í, ó…)
        text = re.sub(
            r"([.!?]\s+)(\w)",
            lambda m: m.group(1) + (m.group(2).upper() if m.group(2).islower() else m.group(2)),
            text,
            flags=re.UNICODE,
        )

        text = text.strip()
        if text:
            text = text[0].upper() + text[1:]
        if text and text[-1] not in ".!?":
            text += "."

        # Restaurer les URLs
        for i, url in enumerate(urls):
            text = text.replace(placeholder.format(i=i), url)

        return text
