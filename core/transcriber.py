"""
Transcription locale via faster-whisper + reformatage du texte.
Le modèle est chargé en lazy (premier appel) et protégé par un lock
pour éviter les race conditions lors des changements de modèle à chaud.
"""

import os
import re
import wave
import logging
import tempfile
import threading
import numpy as np

logger = logging.getLogger(__name__)

FRENCH_FILLERS = [
    "donc voilà", "enfin voilà", "ouais ben", "bon ben",
    "euh", "ben", "bah", "voilà", "enfin", "hein", "quoi",
    "nan nan", "ouais ouais", "ok donc",
]
ENGLISH_FILLERS = [
    "you know", "i mean", "kind of", "sort of",
    "uh", "um", "like", "basically", "literally",
]


class Transcriber:
    def __init__(self, model: str = "small", language: str = "fr", reformatting: bool = True):
        self.model_name  = model
        self.language    = None if language == "auto" else language
        self.reformatting = reformatting
        self._model      = None
        self._model_lock = threading.Lock()

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

    # ── Transcription ─────────────────────────────────────────────────────────

    def transcribe(self, audio_array: np.ndarray) -> str:
        if audio_array is None or len(audio_array) == 0:
            return ""

        with self._model_lock:
            # Capturer une référence locale SOUS le lock. Si update_model() réassigne
            # self._model pendant la transcription, on garde le modèle courant vivant
            # jusqu'à la fin de l'appel (libération auto via refcount ensuite).
            model = self._get_model()

        tmp_path = self._write_wav(audio_array)
        try:
            segments, info = model.transcribe(
                tmp_path,
                language=self.language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()

            detected_lang = info.language if self.language is None else self.language
            if self.reformatting:
                text = self._reformat(text, detected_lang)

            return text

        finally:
            try:
                os.unlink(tmp_path)
            except OSError as e:
                logger.warning(f"Impossible de supprimer le fichier temp {tmp_path}: {e}")

    # ── Audio → WAV ───────────────────────────────────────────────────────────

    def _write_wav(self, audio: np.ndarray) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm.tobytes())
        return tmp.name

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

        # Supprimer les mots de remplissage
        fillers = FRENCH_FILLERS if lang == "fr" else ENGLISH_FILLERS
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

        # Majuscule début de phrase
        text = re.sub(
            r"([.!?]\s+)([a-zàâçéèêëîïôùûüæœ])",
            lambda m: m.group(1) + m.group(2).upper(),
            text,
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
