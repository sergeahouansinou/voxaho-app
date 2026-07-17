"""
Mode « Réunion » — transcription CONTINUE, horodatée et exportable (Voxaho).

Différenciateur vs Wispr : là où la dictée classique est en push-to-talk (on
maintient une touche), le mode Réunion capture l'audio EN CONTINU, le découpe en
fenêtres régulières (~6 s), transcrit chaque fenêtre au fil de l'eau et empile
des segments horodatés affichés en direct. Le tout 100 % local.

Ce moteur est AUTONOME : il n'utilise pas le Recorder push-to-talk existant
(possédé par un autre chantier) ; il ouvre son propre flux `sounddevice` en
continu. Aucune dépendance à Qt ici — pur Python, testable.

IMPORTANT : la capture audio réelle et la transcription live ne peuvent pas être
testées automatiquement (pas de micro en CI). Elles sont donc isolées derrière
deux méthodes mockables — `_open_stream()` et `_transcribe_chunk()` — pour que
les tests couvrent la LOGIQUE (accumulation de segments, horodatage, callback,
export) SANS ouvrir de micro. La capture live reste « à valider en usage réel ».
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

import numpy as np

logger = logging.getLogger(__name__)

# ── Constantes audio + découpage ──────────────────────────────────────────────
SAMPLE_RATE = 16000          # Hz — mono 16 kHz, format attendu par Whisper
CHANNELS = 1
DTYPE = "float32"

# Fenêtre de découpage par défaut : toutes les ~N secondes d'audio accumulé, on
# ferme un chunk et on le transcrit. 6 s est un bon compromis latence/qualité
# (assez court pour un affichage « live », assez long pour une phrase complète).
DEFAULT_WINDOW_SECONDS = 6.0

# En-dessous de cette durée, un chunk (typiquement le reliquat de fin de réunion)
# est ignoré : trop court pour produire une transcription fiable.
_MIN_CHUNK_SECONDS = 0.3

# Mois FR en toutes lettres pour la date lisible de l'export Markdown.
_FR_MONTHS_FULL = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


class MeetingError(RuntimeError):
    """Erreur d'une session Réunion (ex. capture audio indisponible au démarrage).

    Levée par `MeetingSession.start()` quand le flux audio ne peut pas s'ouvrir,
    afin que l'appelant (l'UI) puisse afficher un message clair au lieu de crasher.
    """


# ── Fonctions PURES (testables sans audio ni Qt) ──────────────────────────────

def format_timestamp(seconds: float) -> str:
    """Formate un nombre de secondes en « HH:MM:SS » (ou « MM:SS » si < 1 h).

    Robuste : valeurs None / non numériques / négatives → « 00:00 ». Les
    fractions de seconde sont tronquées.

    Exemples : 0 → "00:00", 65 → "01:05", 3661 → "01:01:01".
    """
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _human_date(dt: datetime) -> str:
    """Date lisible en français : « 17 juillet 2026 à 14:32 »."""
    month = _FR_MONTHS_FULL[dt.month - 1]
    return f"{dt.day} {month} {dt.year} à {dt.hour:02d}:{dt.minute:02d}"


def export_markdown(segments: list[dict], title: str = "Réunion") -> str:
    """Exporte les segments en Markdown : titre + date lisible + liste horodatée.

    Chaque segment devient une ligne `- **[HH:MM:SS]** texte`. Robuste à la liste
    vide (retourne alors le titre et la date seuls). Le timestamp affiché provient
    du champ « timestamp » du segment, avec repli sur format_timestamp(t).
    """
    safe_title = (str(title).strip() if title is not None else "") or "Réunion"
    lines = [f"# {safe_title}", "", f"*{_human_date(datetime.now())}*", ""]
    for seg in (segments or []):
        if not isinstance(seg, dict):
            continue
        ts = seg.get("timestamp") or format_timestamp(seg.get("t", 0))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        lines.append(f"- **[{ts}]** {text}")
    return "\n".join(lines).rstrip() + "\n"


def merge_segments(segments: list[dict]) -> str:
    """Concatène les textes de tous les segments en un transcript continu.

    Fonction pure : ignore les entrées vides / mal formées. Retourne un unique
    paragraphe (segments joints par un espace).
    """
    parts = []
    for seg in (segments or []):
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text", "")).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


# ── Session de réunion (moteur autonome) ──────────────────────────────────────

class MeetingSession:
    """Session de transcription continue horodatée.

    Cycle de vie :
      - `start(on_segment=None)` : ouvre un flux audio continu, précharge le
        modèle en tâche de fond, puis découpe/transcrit l'audio toutes les
        `window_seconds`. `on_segment(segment_dict)` est appelé à chaque nouveau
        segment (typiquement branché sur un signal Qt côté UI).
      - `stop() -> list[dict]` : arrête le flux et retourne tous les segments.
      - `segments` / `is_running()` : lecture d'état, thread-safe.

    segment_dict = {"t": float (s depuis le début), "timestamp": "HH:MM:SS",
    "text": str}.

    Thread-safe (un `Lock` protège l'état partagé) et robuste : la capture et la
    transcription sont entourées de try/except, une session ne crashe jamais en
    cours de route.

    Les points de contact avec le monde réel sont isolés et mockables :
      - `_open_stream(callback)` : ouvre le flux `sounddevice` (capture live à
        valider en usage réel) ;
      - `_transcribe_chunk(audio)` : transcrit un chunk via core.transcriber.
    """

    SAMPLE_RATE = SAMPLE_RATE
    CHANNELS = CHANNELS
    DTYPE = DTYPE

    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS,
                 model: str = "small", device=None, transcriber=None):
        self.window_seconds = float(window_seconds) if window_seconds else DEFAULT_WINDOW_SECONDS
        self.model = model
        self._device = device  # index sounddevice ; None = micro par défaut

        # Transcriber injectable (tests) ; sinon créé paresseusement au 1er usage.
        self._transcriber = transcriber

        self._segments: list[dict] = []
        self._frames: list = []          # buffers audio accumulés par le callback
        self._elapsed = 0.0              # secondes d'audio déjà consommées (horloge du transcript)
        self._running = False
        self._on_segment = None

        self._lock = threading.Lock()    # protège segments / frames / elapsed / running
        self._stream = None
        self._worker = None
        self._stop_event = threading.Event()

    # ── API publique ─────────────────────────────────────────────────────────
    def start(self, on_segment=None) -> None:
        """Démarre la capture continue et le découpage périodique.

        `on_segment` : callback optionnel appelé (hors lock) à chaque nouveau
        segment. Lève `MeetingError` si le flux audio ne peut pas s'ouvrir.
        """
        with self._lock:
            if self._running:
                logger.warning("MeetingSession.start() : une session est déjà en cours")
                return
            self._running = True
            self._segments = []
            self._frames = []
            self._elapsed = 0.0
            self._on_segment = on_segment
            self._stop_event = threading.Event()

        # Ouverture du flux audio (isolée → mockable). Échec = on annule proprement
        # et on remonte l'erreur pour que l'UI l'affiche.
        try:
            self._stream = self._open_stream(self._audio_callback)
        except Exception as e:
            with self._lock:
                self._running = False
            logger.error("MeetingSession : ouverture du flux audio impossible : %s", e)
            raise MeetingError(f"Capture audio indisponible : {e}") from e

        # Préchargement du modèle en tâche de fond (best-effort, ne bloque jamais
        # le thread appelant ; le modèle sera de toute façon chargé au 1er chunk).
        try:
            transcriber = self._get_transcriber()
            threading.Thread(target=transcriber.preload, daemon=True).start()
        except Exception as e:  # pragma: no cover - purement défensif
            logger.warning("MeetingSession : préchargement du modèle ignoré : %s", e)

        # Worker de découpage périodique.
        self._worker = threading.Thread(target=self._run_loop, daemon=True)
        self._worker.start()

    def stop(self) -> list[dict]:
        """Arrête la session et retourne la liste complète des segments.

        Thread-safe et idempotent : un `stop()` sur une session déjà arrêtée
        renvoie simplement les segments courants.
        """
        with self._lock:
            if not self._running:
                return list(self._segments)
            self._running = False
            stream = self._stream
            self._stream = None
            stop_event = self._stop_event
            worker = self._worker
            self._worker = None

        # Réveille et attend le worker (borné : on ne bloque jamais l'UI indéfiniment).
        stop_event.set()
        if worker is not None:
            try:
                worker.join(timeout=self.window_seconds * 2 + 5)
            except Exception as e:  # pragma: no cover - purement défensif
                logger.warning("MeetingSession : join worker : %s", e)

        # Fermeture du flux HORS lock (sounddevice peut attendre le callback).
        try:
            if stream is not None:
                stream.stop()
        except Exception as e:
            logger.warning("MeetingSession : stream.stop() : %s", e)
        try:
            if stream is not None:
                stream.close()
        except Exception as e:
            logger.warning("MeetingSession : stream.close() : %s", e)

        # Flush final de l'audio résiduel (le drain est verrouillé → sans risque
        # de double traitement même si le worker vient tout juste de tourner).
        try:
            self._flush_chunk()
        except Exception as e:  # pragma: no cover - purement défensif
            logger.warning("MeetingSession : flush final : %s", e)

        with self._lock:
            return list(self._segments)

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def segments(self) -> list[dict]:
        """Copie thread-safe de la liste des segments accumulés."""
        with self._lock:
            return list(self._segments)

    # ── Points de contact « monde réel » (isolés → mockables) ─────────────────
    def _open_stream(self, callback):
        """Ouvre et démarre un flux `sounddevice` continu (16 kHz mono float32).

        Isolé pour être mockable dans les tests. CAPTURE LIVE À VALIDER EN USAGE
        RÉEL (aucun micro en CI). Retourne l'objet stream déjà démarré.
        """
        import sounddevice as sd  # import différé : jamais requis pour les tests
        stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
            dtype=self.DTYPE,
            blocksize=1024,
            device=self._device,
            callback=callback,
        )
        stream.start()
        return stream

    def _transcribe_chunk(self, audio: np.ndarray) -> str:
        """Transcrit un chunk audio via core.transcriber. Retourne "" en cas d'échec.

        Isolé pour être mockable dans les tests (transcription live à valider en
        usage réel). Ne lève jamais : une session ne doit pas crasher sur un chunk.
        """
        try:
            return self._get_transcriber().transcribe(audio) or ""
        except Exception as e:
            logger.warning("MeetingSession : transcription d'un chunk échouée : %s", e)
            return ""

    def _get_transcriber(self):
        """Retourne le Transcriber interne (créé paresseusement, modèle 'small')."""
        if self._transcriber is None:
            from core.transcriber import Transcriber
            self._transcriber = Transcriber(model=self.model)
        return self._transcriber

    # ── Mécanique interne ─────────────────────────────────────────────────────
    def _audio_callback(self, indata, frames, time_info, status):
        """Callback sounddevice : empile une copie du buffer si la session tourne."""
        with self._lock:
            if not self._running:
                return
            frames_list = self._frames
        # Copie hors lock pour rester rapide (le callback est temps réel).
        frames_list.append(indata.copy())

    def _run_loop(self):
        """Boucle worker : toutes les `window_seconds`, ferme et transcrit un chunk."""
        while True:
            triggered = self._stop_event.wait(self.window_seconds)
            if triggered:
                break  # stop() prend en charge le flush final
            try:
                self._flush_chunk()
            except Exception as e:  # pragma: no cover - purement défensif
                logger.warning("MeetingSession : flush périodique échoué : %s", e)

    def _drain_frames(self) -> np.ndarray | None:
        """Vide (sous lock) l'audio accumulé et le concatène en un seul tableau."""
        with self._lock:
            frames = self._frames
            self._frames = []
        if not frames:
            return None
        try:
            return np.concatenate(frames, axis=0).flatten()
        except Exception as e:  # pragma: no cover - purement défensif
            logger.warning("MeetingSession : concaténation audio échouée : %s", e)
            return None

    def _flush_chunk(self):
        """Ferme le chunk courant (audio accumulé) et l'ajoute comme segment."""
        audio = self._drain_frames()
        if audio is None or len(audio) < self.SAMPLE_RATE * _MIN_CHUNK_SECONDS:
            return
        self._add_segment_from_chunk(audio)

    def _add_segment_from_chunk(self, audio: np.ndarray):
        """Transcrit un chunk et ajoute un segment horodaté si le texte est non vide.

        C'est le SEAM de test : les tests injectent de faux chunks en monkeypatchant
        `_transcribe_chunk`, puis appellent cette méthode directement. L'horloge du
        transcript (`t`) est fondée sur la durée d'audio consommée (déterministe,
        indépendante de l'horloge murale), ce qui garantit des timestamps croissants.
        """
        try:
            duration = len(audio) / self.SAMPLE_RATE
        except TypeError:
            duration = 0.0

        # Réserve la position temporelle de ce chunk et avance l'horloge, même si
        # la transcription est vide (le temps s'écoule quand même).
        with self._lock:
            chunk_start = self._elapsed
            self._elapsed += duration

        text = self._transcribe_chunk(audio)
        if not text or not str(text).strip():
            return None

        segment = {
            "t": float(chunk_start),
            "timestamp": format_timestamp(chunk_start),
            "text": str(text).strip(),
        }
        with self._lock:
            self._segments.append(segment)
            callback = self._on_segment

        # Callback hors lock (il peut ré-entrer / émettre un signal Qt).
        if callback is not None:
            try:
                callback(segment)
            except Exception as e:
                logger.warning("MeetingSession : callback on_segment a levé : %s", e)
        return segment
