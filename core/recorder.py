"""
Capture audio via sounddevice pendant que Fn est maintenu.
Retourne un tableau numpy float32 mono 16 kHz prêt pour Whisper.
"""

import logging
import threading
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS    = 1
DTYPE       = "float32"
MIN_SECONDS = 0.1   # ignore les appuis accidentels < 0.1 s


class Recorder:
    def __init__(self):
        self._frames  = []
        self._stream  = None
        self._active  = False
        self._lock    = threading.Lock()  # vrai lock contre re-entrée et race start/stop/callback

    def start(self):
        with self._lock:
            if self._active:
                logger.warning("Recorder: start() appelé alors qu'un enregistrement est déjà actif")
                return
            self._frames = []
            self._active = True
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=1024,
                callback=self._callback,
            )
            self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        # Copier hors du lock pour rester rapide ; le lock protège la vérif d'état
        with self._lock:
            if not (self._active and self._stream):
                return
            frames_list = self._frames
        frames_list.append(indata.copy())

    def stop(self) -> np.ndarray | None:
        """Arrête et retourne l'audio float32 16 kHz, ou None si trop court."""
        with self._lock:
            self._active = False
            stream = self._stream
            self._stream = None
            frames = self._frames
            self._frames = []

        # Stop/close hors du lock — sounddevice peut bloquer en attendant le callback,
        # ce qui causerait un deadlock si le callback tente d'acquérir le même lock.
        try:
            if stream:
                stream.stop()
        except Exception as e:
            logger.warning(f"Recorder stream.stop(): {e}")
        try:
            if stream:
                stream.close()
        except Exception as e:
            logger.warning(f"Recorder stream.close(): {e}")

        if not frames:
            return None

        audio = np.concatenate(frames, axis=0).flatten()

        if len(audio) < SAMPLE_RATE * MIN_SECONDS:
            return None

        return audio
