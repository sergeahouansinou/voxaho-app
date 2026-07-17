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
            # Filtrer les hallucinations de Whisper générées sur silence/bruit :
            # segments quasi muets peu fiables + phrases fantômes de la blocklist.
            textes = []
            for seg in segments:
                seg_text = seg.text.strip()
                if not seg_text:
                    continue
                no_speech_prob = getattr(seg, "no_speech_prob", 0.0)
                avg_logprob    = getattr(seg, "avg_logprob", 0.0)
                if no_speech_prob > 0.6 and avg_logprob < -0.8:
                    continue  # probablement du silence mal interprété
                if _is_hallucination(seg_text):
                    continue  # phrase fantôme connue (ex. crédits de sous-titres)
                textes.append(seg_text)
            text = " ".join(textes).strip()

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
