"""
Barre flottante principale de Voxaho.

États visuels :
  Mini (souris absente) → pilule 20×5px colorée selon l'état
  Idle (survol)         → barre 340×44px, dot vert + hint touche
  Recording             → waveform rouge animée
  Processing            → dots pulsants bleus
  Error                 → message rouge 2.5 s
"""

import sys
import math
import time
import logging
import threading
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QRect, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPainterPath, QPen, QBrush, QFontMetrics,
    QLinearGradient, QRadialGradient,
)
from core.hotkey import HOTKEY_HINT, HOTKEY_LABEL


# ── Easing ────────────────────────────────────────────────────────────────────
def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _ease_in_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t ** 3


def _ease_in_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4 * t ** 3
    return 1 - ((-2 * t + 2) ** 3) / 2


# Durées (secondes)
DUR_SHOW_IN   = 0.20   # ease-out-cubic
DUR_SHOW_OUT  = 0.18   # ease-in-cubic
DUR_EXPAND    = 0.25   # ease-in-out-cubic
DUR_REC_PULSE = 0.20   # bump scale au passage RECORDING

IS_MAC = sys.platform == "darwin"
logger = logging.getLogger(__name__)

# Titre unique de la fenêtre barre (invisible : fenêtre frameless) — sert à
# cibler UNIQUEMENT la NSWindow de la barre dans _force_always_on_top (M6).
BAR_WINDOW_TITLE = "VoxahoBar"

# Marge appliquée quand la barre doit être ramenée dans la zone visible (M5)
CLAMP_MARGIN = 8


def clamp_to_screen(x: int, y: int, w: int, h: int,
                    screen_rect: tuple[int, int, int, int]) -> tuple[int, int]:
    """Ramène le rectangle (x, y, w, h) dans la zone visible de l'écran (M5).

    Fonction pure (testable sans Qt). `screen_rect` = (sx, sy, sw, sh).

    Règles :
      - Coordonnées déjà entièrement visibles → inchangées (x=0 sur le bord
        gauche est une position légitime, elle est conservée).
      - Coordonnées hors écran (ex. écran externe débranché) → ramenées dans
        la zone visible avec une marge de CLAMP_MARGIN px du bord.
      - Barre plus grande que l'écran → coin haut-gauche + marge.
    """
    sx, sy, sw, sh = screen_rect

    def _clamp_axis(v: int, size: int, s0: int, s_len: int) -> int:
        lo = s0                  # première position entièrement visible
        hi = s0 + s_len - size   # dernière position entièrement visible
        if hi < lo:
            # Barre plus grande que l'écran sur cet axe → bord haut/gauche + marge
            return s0 + CLAMP_MARGIN
        if v < lo:
            return min(lo + CLAMP_MARGIN, hi)
        if v > hi:
            return max(hi - CLAMP_MARGIN, lo)
        return v  # position valide : ne pas toucher

    return (int(_clamp_axis(int(x), int(w), int(sx), int(sw))),
            int(_clamp_axis(int(y), int(h), int(sy), int(sh))))

# ── Dimensions ────────────────────────────────────────────────────────────────
MINI_W         = 40   # défaut "medium" (override possible par config: mini_size)
MINI_H         = 8

BAR_W          = 340
BAR_H          = 44
BAR_H_EXPANDED = 190
RADIUS         = 22
MINI_RADIUS    = 3

# Mapping taille mini-bar (slider Apparence)
MINI_SIZES = {
    "small":  (30, 6),
    "medium": (40, 8),
    "large":  (52, 10),
}

# ── Palette ───────────────────────────────────────────────────────────────────
# Couleurs sémantiques fixes (rouge = REC, vert = OK) — non remplacées par l'accent.
C_BG_IDLE      = QColor(28,  28,  30,  228)
C_BG_REC       = QColor(50,  18,  18,  235)
C_BORDER       = QColor(80,  80,  80,  90)
C_BORDER_REC   = QColor(255, 59,  48,  160)
C_DOT_IDLE     = QColor(52,  199, 89)
C_DOT_REC      = QColor(255, 59,  48)
C_TEXT_PRIMARY = QColor(255, 255, 255, 220)
C_TEXT_MUTED   = QColor(150, 150, 155, 190)

# Palette accent (id → primary/bg/border) — change couleur du PROCESSING, lien
# "Personnaliser…" et autres éléments accent.
ACCENT_PALETTES = {
    "blue":   {"primary": QColor(10,  132, 255), "bg": QColor(15, 25, 45, 235), "border": QColor(10,  132, 255, 160)},
    "purple": {"primary": QColor(191, 90,  242), "bg": QColor(35, 15, 45, 235), "border": QColor(191, 90,  242, 160)},
    "green":  {"primary": QColor(48,  209, 88),  "bg": QColor(15, 30, 18, 235), "border": QColor(48,  209, 88,  160)},
    "orange": {"primary": QColor(255, 159, 10),  "bg": QColor(45, 30, 15, 235), "border": QColor(255, 159, 10,  160)},
    "pink":   {"primary": QColor(255, 55,  95),  "bg": QColor(45, 15, 25, 235), "border": QColor(255, 55,  95,  160)},
}


def open_workspace_link_geom(bar_h: int) -> tuple[int, int, int, int]:
    """Rectangle (x, y, w, h) du lien « Ouvrir Voxaho » du panneau étendu (Phase 1).

    Fonction pure (testable sans Qt) — garde le DESSIN (_draw_settings_panel) et
    le HIT-TEST (mousePressEvent) parfaitement cohérents. Le lien est calé sur la
    même ligne que « ⚙ Personnaliser… » (custom_y), placé à droite pour ne pas
    empiéter sur son rectangle cliquable (14 → 183) ni déborder de la barre.

    NB : la formule custom_y est identique à celle du dessin/hit-test de
    « Personnaliser… » (bar_h + 16 + 4 lignes de 26 px + 10) → toute retouche de
    l'une doit suivre l'autre.
    """
    custom_y = bar_h + 16 + 4 * 26 + 10
    return (184, custom_y - 16, 152, 24)


class FloatingBar(QWidget):

    _transcription_signal = pyqtSignal(str)
    _state_signal         = pyqtSignal(str)

    IDLE       = "idle"
    RECORDING  = "recording"
    PROCESSING = "processing"
    ERROR      = "error"

    def __init__(self, config: dict, save_config_fn):
        super().__init__()
        self.config       = config
        self._save_config = save_config_fn
        self.state        = self.IDLE
        self._hovered     = False

        # Animation
        self._show_frac     = 0.0
        self._target_show   = 0.0
        self._expanded_frac = 0.0
        self._target_frac   = 0.0
        self._wave_heights  = [0.25] * 8
        self._anim_offset   = 0.0
        self._proc_phase    = 0.0

        # Easing temporel (progress 0→1 sur une durée)
        self._show_start    = 0.0
        self._show_from     = 0.0
        self._show_to       = 0.0
        self._show_duration = DUR_SHOW_IN
        self._show_ease     = _ease_out_cubic

        self._exp_start     = 0.0
        self._exp_from      = 0.0
        self._exp_to        = 0.0
        self._exp_duration  = DUR_EXPAND

        # Pulse RECORDING + glow
        self._rec_pulse_start: float | None = None
        self._glow_phase    = 0.0
        self._mini_phase    = 0.0

        # Fenêtre settings (référence GC)
        self._settings_win = None

        # Fenêtre Workspace / fenêtre principale (référence GC, Phase 1)
        self._workspace_win = None

        # Ancrage géométrique
        self._anchor_cx = 0
        self._anchor_cy = 0

        # Apparence (initialisée depuis self.config)
        self._mini_w        = MINI_W
        self._mini_h        = MINI_H
        self._accent_id     = "blue"
        self._accent_primary = ACCENT_PALETTES["blue"]["primary"]
        self._accent_bg      = ACCENT_PALETTES["blue"]["bg"]
        self._accent_border  = ACCENT_PALETTES["blue"]["border"]
        self._apply_appearance(self.config)

        # Drag
        self._drag_start: QPoint | None = None

        # Objets métier
        self._recorder            = None
        self._transcriber         = None
        self._transcription_thread: threading.Thread | None = None
        self._transcription_cancelled = threading.Event()
        self._error_timer: QTimer | None = None   # référence gardée pour éviter GC

        self._setup_window()
        self._setup_transcriber()
        self._setup_hotkey()
        self._setup_timers()

        self._transcription_signal.connect(self._on_transcription)
        self._state_signal.connect(self._set_state)

    # ── Initialisation ────────────────────────────────────────────────────────

    def _apply_appearance(self, cfg: dict):
        """Met à jour les attributs visuels (taille mini, palette accent) depuis cfg."""
        size = cfg.get("mini_size", "medium")
        self._mini_w, self._mini_h = MINI_SIZES.get(size, MINI_SIZES["medium"])

        accent = cfg.get("accent", "blue")
        palette = ACCENT_PALETTES.get(accent, ACCENT_PALETTES["blue"])
        self._accent_id      = accent
        self._accent_primary = palette["primary"]
        self._accent_bg      = palette["bg"]
        self._accent_border  = palette["border"]

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        # Titre unique — invisible (frameless) mais permet à
        # _force_always_on_top de ne cibler QUE la NSWindow de la barre (M6)
        self.setWindowTitle(BAR_WINDOW_TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(1, 1)
        self.setMaximumSize(BAR_W + 10, BAR_H_EXPANDED + 10)

        screen  = QApplication.primaryScreen().geometry()
        # `is not None` : bar_x=0 (bord gauche) est une valeur légitime — un
        # simple `or` la traitait comme absente (M5)
        saved_x = self.config.get("bar_x")
        saved_y = self.config.get("bar_y")
        full_x  = saved_x if saved_x is not None else (screen.width()  - BAR_W) // 2
        full_y  = saved_y if saved_y is not None else (screen.height() - BAR_H - 64)

        # Re-borner : après un changement d'écran (externe débranché…), les
        # coords sauvegardées peuvent être hors de toute zone visible (M5)
        full_x, full_y = clamp_to_screen(
            int(full_x), int(full_y), BAR_W, BAR_H,
            (screen.x(), screen.y(), screen.width(), screen.height()),
        )

        self._anchor_cx = full_x + BAR_W // 2
        self._anchor_cy = full_y + BAR_H // 2

        self._apply_geometry()

    def _apply_geometry(self):
        sf = self._show_frac
        ef = self._expanded_frac
        scale = self._rec_pulse_scale()

        mw, mh = self._mini_w, self._mini_h
        cw   = max(mw, int((mw + (BAR_W - mw) * sf) * scale))
        ch_b = max(mh, int((mh + (BAR_H - mh) * sf) * scale))
        ch_e = int((BAR_H_EXPANDED - BAR_H) * ef * sf)
        ch   = ch_b + ch_e

        cx = self._anchor_cx - cw // 2
        cy = self._anchor_cy - ch_b // 2

        self.resize(cw, ch)
        self.move(cx, cy)

    def showEvent(self, event):
        super().showEvent(event)
        self._force_always_on_top()

    def _force_always_on_top(self):
        """NSModalPanelWindowLevel (8) + CanJoinAllSpaces + IgnoresCycle.

        Cible UNIQUEMENT la NSWindow de la barre via son titre unique
        (BAR_WINDOW_TITLE) — sinon toutes les fenêtres NSApp (Préférences,
        wizard…) passaient aussi en niveau modal-panel + toutes-les-spaces (M6).
        """
        if not IS_MAC:
            return
        try:
            from AppKit import NSApp
            for win in NSApp.windows():
                if win.title() != BAR_WINDOW_TITLE:
                    continue  # ne pas toucher aux autres fenêtres (M6)
                win.setLevel_(8)  # NSModalPanelWindowLevel — passe devant Spotlight
                win.setCollectionBehavior_(
                    1   |  # NSWindowCollectionBehaviorCanJoinAllSpaces
                    16  |  # NSWindowCollectionBehaviorStationary
                    128    # NSWindowCollectionBehaviorIgnoresCycle (hors Cmd+Tab)
                )
        except Exception as e:
            logger.warning(f"_force_always_on_top: {e}")

    def _setup_transcriber(self):
        self._transcriber = self._build_transcriber()
        # Préchargement au démarrage (LE gain majeur du ressenti) : charge +
        # warmup le modèle dans un thread daemon pour que la 1ʳᵉ dictée n'attende
        # plus 2-5 s. preload() est thread-safe / idempotent / ne lève jamais.
        self._launch_preload()

    def _build_transcriber(self):
        """Instancie un Transcriber avec la config courante (beam_size + backend).

        Défensif : si le constructeur de l'agent moteur ne connaît pas encore les
        kwargs `beam_size`/`backend` (ordre d'arrivée des agents parallèles), on
        retombe sur la signature minimale. Transitoire — à retirer une fois le
        contrat moteur stabilisé.
        """
        from core.transcriber import Transcriber
        model        = self.config.get("model",           "small")
        language     = self.config.get("language",        "fr")
        reformatting = self.config.get("reformatting",    True)
        beam_size    = self.config.get("beam_size",       1)
        backend      = self.config.get("compute_backend", "auto")
        try:
            return Transcriber(
                model        = model,
                language     = language,
                reformatting = reformatting,
                beam_size    = beam_size,
                backend      = backend,
            )
        except TypeError:
            # Transitoire : constructeur moteur pas encore à jour → sans kwargs.
            logger.warning("Transcriber sans kwargs beam_size/backend (contrat moteur transitoire)")
            return Transcriber(
                model        = model,
                language     = language,
                reformatting = reformatting,
            )

    def _launch_preload(self):
        """Lance preload() du transcriber dans un thread daemon (défensif)."""
        preload = getattr(self._transcriber, "preload", None)
        if not callable(preload):
            return  # contrat moteur transitoire : preload() pas encore dispo
        threading.Thread(target=preload, daemon=True, name="voxaho-preload").start()

    def _apply_beam_size(self, beam_size: int):
        """Applique le beam_size à chaud : via update_settings si dispo, sinon setattr."""
        update = getattr(self._transcriber, "update_settings", None)
        if callable(update):
            try:
                update(beam_size=beam_size)
                return
            except TypeError:
                pass  # transitoire : update_settings sans kwarg beam_size
        # Repli : attribut direct (le moteur expose self.beam_size)
        try:
            self._transcriber.beam_size = beam_size
        except Exception as e:
            logger.warning(f"_apply_beam_size: {e}")

    def _recreate_transcriber(self):
        """Recrée le Transcriber (backend changé) : unload → new → preload.

        Le backend de calcul est fixé au constructeur ; le changer impose une
        reconstruction complète plutôt qu'un simple réglage à chaud.
        """
        old = self._transcriber
        if old is not None:
            try:
                old.unload_model()
            except Exception as e:
                logger.warning(f"_recreate_transcriber — unload ancien: {e}")
        self._transcriber = self._build_transcriber()
        self._launch_preload()

    def _setup_hotkey(self):
        self._start_hotkey(self.config.get("win_key", "ctrl_r"))

    def _start_hotkey(self, win_key: str):
        """Crée le HotkeyListener, connecte ses 4 signaux et le démarre."""
        from core.hotkey import HotkeyListener
        self._hotkey = HotkeyListener(win_key=win_key)
        self._hotkey.fn_pressed.connect(self._on_fn_press)
        self._hotkey.fn_released.connect(self._on_fn_release)
        self._hotkey.permission_error.connect(self._on_permission_error)
        self._hotkey.permission_restored.connect(self._on_permission_restored)
        self._hotkey.start()

    def _restart_hotkey(self, win_key: str):
        """Reconfigure la touche de dictée à chaud (M1).

        Arrête proprement l'ancien listener puis en démarre un nouveau avec
        la nouvelle touche — sans ça, un changement de touche dans les
        Préférences n'était pris en compte qu'au redémarrage.
        """
        try:
            if getattr(self, "_hotkey", None) is not None:
                self._hotkey.stop()
        except Exception as e:
            logger.warning(f"_restart_hotkey — arrêt ancien listener: {e}")
        self._start_hotkey(win_key)

    def _setup_timers(self):
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(40)

    # ── Nettoyage à la fermeture ──────────────────────────────────────────────

    def closeEvent(self, event):
        self._anim_timer.stop()

        # Arrêter le hotkey listener
        if hasattr(self, "_hotkey"):
            self._hotkey.stop()

        # Arrêter l'enregistrement en cours
        if self._recorder:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._recorder = None

        # Signaler l'annulation puis attendre la fin de la transcription (max 5 s)
        self._transcription_cancelled.set()
        if self._transcription_thread and self._transcription_thread.is_alive():
            self._transcription_thread.join(timeout=5.0)

        # Libérer le modèle Whisper (3+ GB de RAM)
        if self._transcriber:
            self._transcriber.unload_model()

        super().closeEvent(event)

    # ── Hotkey ────────────────────────────────────────────────────────────────

    def _on_fn_press(self):
        if self.state != self.IDLE:
            return

        # Nettoyer un éventuel recorder précédent non fermé
        if self._recorder:
            try:
                self._recorder.stop()
            except Exception:
                pass

        self._state_signal.emit(self.RECORDING)

        try:
            from core.recorder import Recorder
            device = self.config.get("input_device")
            try:
                self._recorder = Recorder(device=device)
            except TypeError:
                # Transitoire : Recorder pas encore doté du kwarg `device`.
                self._recorder = Recorder()
            self._recorder.start()
        except Exception as e:
            logger.error(f"Recorder start: {e}")
            self._state_signal.emit(self.ERROR)

    def _on_fn_release(self):
        if self.state != self.RECORDING:
            return
        if not self._recorder:
            self._state_signal.emit(self.IDLE)
            return
        self._state_signal.emit(self.PROCESSING)

        audio          = self._recorder.stop()
        self._recorder = None

        if audio is None:
            self._state_signal.emit(self.IDLE)
            return

        self._transcription_cancelled.clear()

        def _run():
            try:
                if self._transcription_cancelled.is_set():
                    return
                text = self._transcriber.transcribe(audio)
                if self._transcription_cancelled.is_set():
                    return
                if text:
                    self._transcription_signal.emit(text)
                else:
                    self._state_signal.emit(self.IDLE)
            except Exception as e:
                logger.error(f"Transcription: {e}", exc_info=True)
                if not self._transcription_cancelled.is_set():
                    self._state_signal.emit(self.ERROR)

        # daemon=True + cancellation flag → shutdown jamais bloqué par ce thread
        self._transcription_thread = threading.Thread(target=_run, daemon=True, name="Transcription")
        self._transcription_thread.start()

    def _on_permission_error(self):
        # Erreur de permission = persistante (pas de timer auto-recovery 2.5s
        # comme les autres erreurs), pour laisser l'utilisateur cliquer sur
        # les boutons "Ouvrir Réglages" / "Redémarrer".
        self.state = self.ERROR
        self._target_show = 1.0
        # Annule un timer précédent si présent
        if self._error_timer:
            self._error_timer.stop()
            self._error_timer = None
        self.update()

    def _on_permission_restored(self):
        """L'Accessibilité vient d'être accordée — sortir de l'état d'erreur."""
        logger.info("Permission accordée — retour à IDLE")
        if self.state == self.ERROR:
            # Stoppe le timer 2.5 s si encore actif
            if self._error_timer:
                self._error_timer.stop()
                self._error_timer = None
            self._state_signal.emit(self.IDLE)

    def _on_transcription(self, text: str):
        """Slot Qt (thread principal) — délègue l'injection à un thread dédié.

        inject_text contient des sleeps bloquants (délai copie→collage, voire
        restauration presse-papiers) : l'exécuter ici gelait l'UI ~200 ms à
        chaque dictée (C4a). Le retour à IDLE (ou ERROR) se fait via
        _state_signal, déjà thread-safe.
        """
        from core.injector import inject_text

        def _run():
            try:
                inject_text(text)
            except Exception as e:
                logger.error(f"Injection: {e}", exc_info=True)
                self._state_signal.emit(self.ERROR)
            else:
                # Injection réussie : enregistrer la dictée dans l'historique
                # (best-effort) PUIS repasser à IDLE. Fait ici — thread
                # d'injection, après l'inject — pour ne rien ajouter au chemin
                # critique de la dictée (Phase 1).
                self._record_dictation(text)
                self._state_signal.emit(self.IDLE)

        threading.Thread(target=_run, daemon=True, name="Injection").start()

    def _record_dictation(self, text: str):
        """Enregistre la dictée dans l'historique local puis rafraîchit le Workspace.

        Contrat couche données fourni par un agent parallèle (core.history) : tout
        est encapsulé DÉFENSIVEMENT — un échec d'historique ne doit JAMAIS casser
        la dictée ni l'UI. add_entry gère déjà le flag d'activation (is_enabled),
        un simple appel suffit.

        Appelé depuis le thread d'injection : l'émission d'un signal Qt reste
        thread-safe (connexion queued automatique vers le thread GUI).
        """
        try:
            from core import history
            history.add_entry(
                text,
                language   = self.config.get("language"),
                model      = self.config.get("model"),
                word_count = len(text.split()),
            )
        except Exception as e:
            logger.warning(f"Historique add_entry: {e}")

        # Rafraîchissement live du Workspace s'il est ouvert (§4)
        win = getattr(self, "_workspace_win", None)
        if win is not None:
            try:
                win.dictation_added.emit()
            except Exception as e:
                logger.warning(f"Workspace refresh (dictation_added): {e}")

    def _set_state(self, state: str):
        prev_state = self.state
        self.state = state

        if state == self.RECORDING and prev_state != self.RECORDING:
            self._rec_pulse_start = time.monotonic()

        if state in (self.RECORDING, self.PROCESSING):
            self._set_target_show(1.0)
        elif state == self.ERROR:
            self._set_target_show(1.0)
            # Garder référence au timer pour éviter destruction par le GC
            if self._error_timer:
                self._error_timer.stop()
            self._error_timer = QTimer(self)
            self._error_timer.setSingleShot(True)
            self._error_timer.timeout.connect(lambda: self._set_state(self.IDLE))
            self._error_timer.start(2500)
        elif state == self.IDLE:
            if not self._hovered:
                self._set_target_show(0.0)

    # ── Transitions easing ────────────────────────────────────────────────────
    def _set_target_show(self, target: float):
        if abs(target - self._target_show) < 0.001 and abs(target - self._show_frac) < 0.001:
            return
        self._target_show   = target
        self._show_from     = self._show_frac
        self._show_to       = target
        self._show_start    = time.monotonic()
        if target > self._show_frac:
            self._show_duration = DUR_SHOW_IN
            self._show_ease     = _ease_out_cubic
        else:
            self._show_duration = DUR_SHOW_OUT
            self._show_ease     = _ease_in_cubic

    def _set_target_expand(self, target: float):
        if abs(target - self._target_frac) < 0.001 and abs(target - self._expanded_frac) < 0.001:
            return
        self._target_frac  = target
        self._exp_from     = self._expanded_frac
        self._exp_to       = target
        self._exp_start    = time.monotonic()
        self._exp_duration = DUR_EXPAND

    # ── Animation tick ────────────────────────────────────────────────────────

    def _tick(self):
        changed = False
        now = time.monotonic()

        if self.state == self.RECORDING:
            self._anim_offset += 0.18
            for i in range(len(self._wave_heights)):
                self._wave_heights[i] = 0.2 + 0.8 * abs(math.sin(self._anim_offset + i * 0.75))
            self._glow_phase += 0.04  # respiration glow ~1.5 s
            changed = True
        elif self.state == self.PROCESSING:
            self._proc_phase += 0.12
            changed = True
        else:
            for i in range(len(self._wave_heights)):
                self._wave_heights[i] = max(0.2, self._wave_heights[i] * 0.85)
            self._mini_phase += 0.012  # ondulation lente mini-bar idle
            if self._show_frac < 0.08:
                changed = True

        # Easing temporel show
        if abs(self._show_frac - self._show_to) > 0.001:
            t = (now - self._show_start) / max(0.001, self._show_duration)
            if t >= 1.0:
                self._show_frac = self._show_to
            else:
                eased = self._show_ease(t)
                self._show_frac = self._show_from + (self._show_to - self._show_from) * eased
            changed = True

        # Easing temporel expand
        if abs(self._expanded_frac - self._exp_to) > 0.001:
            t = (now - self._exp_start) / max(0.001, self._exp_duration)
            if t >= 1.0:
                self._expanded_frac = self._exp_to
            else:
                eased = _ease_in_out_cubic(t)
                self._expanded_frac = self._exp_from + (self._exp_to - self._exp_from) * eased
            changed = True

        # Pulse RECORDING actif
        if self._rec_pulse_start is not None:
            if now - self._rec_pulse_start > DUR_REC_PULSE:
                self._rec_pulse_start = None
            changed = True

        if changed:
            self._apply_geometry()
            self.update()

    # ── Pulse scale courant pour le rendu ─────────────────────────────────────
    def _rec_pulse_scale(self) -> float:
        if self._rec_pulse_start is None:
            return 1.0
        t = (time.monotonic() - self._rec_pulse_start) / DUR_REC_PULSE
        if t >= 1.0:
            return 1.0
        # 0 → 0.5 : montée vers 1.05 ; 0.5 → 1 : retour à 1.0
        if t < 0.5:
            return 1.0 + 0.05 * _ease_out_cubic(t * 2)
        return 1.05 - 0.05 * _ease_out_cubic((t - 0.5) * 2)

    # ── Peinture ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._show_frac < 0.08:
            self._draw_mini(painter)
        else:
            bar_h  = max(self._mini_h, int(self._mini_h + (BAR_H - self._mini_h) * self._show_frac))
            full_h = bar_h + int((BAR_H_EXPANDED - BAR_H) * self._expanded_frac * self._show_frac)
            w      = self.width()

            self._draw_bg(painter, w, full_h)
            if self._show_frac > 0.3:
                self._draw_pill(painter, w, bar_h)
            if self._expanded_frac > 0.05 and self._show_frac > 0.7:
                if self.state == self.ERROR:
                    self._draw_error_actions(painter, w, bar_h)
                else:
                    self._draw_settings_panel(painter, w, bar_h)

        painter.end()

    def _draw_mini(self, painter: QPainter):
        # Idle utilise la couleur accent (était C_DOT_IDLE figé bleu/vert) ;
        # RECORDING garde rouge, PROCESSING utilise accent.
        color_map = {
            self.IDLE:       self._accent_primary,
            self.RECORDING:  C_DOT_REC,
            self.PROCESSING: self._accent_primary,
            self.ERROR:      QColor(255, 69, 58),
        }
        color = color_map.get(self.state, self._accent_primary)
        # Radius interpolé entre MINI_RADIUS (mini) et RADIUS (barre) selon show_frac
        radius = MINI_RADIUS + (RADIUS - MINI_RADIUS) * self._show_frac
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        if self.state == self.IDLE:
            # Léger gradient horizontal animé (ondulation lente)
            phase = (math.sin(self._mini_phase) + 1) / 2  # 0..1
            grad = QLinearGradient(0, 0, self.width(), 0)
            c1 = QColor(color)
            c2 = QColor(color.red(), color.green(), color.blue(), 200)
            c1.setAlpha(255)
            grad.setColorAt(max(0.0, 0.0 + 0.1 * phase), c2)
            grad.setColorAt(0.5, c1)
            grad.setColorAt(min(1.0, 1.0 - 0.1 * phase), c2)
            painter.fillPath(path, QBrush(grad))
        else:
            painter.fillPath(path, color)

    def _draw_bg(self, painter: QPainter, w: int, h: int):
        # Glow halo respirant en RECORDING
        if self.state == self.RECORDING and self._show_frac > 0.5:
            # alpha 60 → 120 → 60 sur ~1.5 s
            breath = (math.sin(self._glow_phase) + 1) / 2  # 0..1
            glow_a = int(60 + 60 * breath)
            for i, (off, fact) in enumerate(((6, 0.25), (3, 0.55), (1, 0.85))):
                rect = QRectF(-off, -off, w + 2 * off, h + 2 * off)
                gp = QPainterPath()
                gp.addRoundedRect(rect, RADIUS + off, RADIUS + off)
                painter.fillPath(gp, QColor(255, 59, 48, int(glow_a * fact)))

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), RADIUS, RADIUS)

        style = {
            self.RECORDING: (C_BG_REC,           C_BORDER_REC),
            self.PROCESSING:(self._accent_bg,    self._accent_border),
            self.ERROR:     (QColor(70, 18, 18, 235), QColor(255, 69, 58, 200)),
        }
        bg, border = style.get(self.state, (C_BG_IDLE, C_BORDER))

        a      = int(self._show_frac * 255)
        bg     = QColor(bg.red(),     bg.green(),     bg.blue(),     min(a, bg.alpha()))
        border = QColor(border.red(), border.green(), border.blue(), min(a, border.alpha()))

        painter.fillPath(path, bg)
        painter.setPen(QPen(border, 0.8))
        painter.drawPath(path)

    def _draw_pill(self, painter: QPainter, w: int, bar_h: int):
        cy    = bar_h // 2
        alpha = int(min(1.0, (self._show_frac - 0.3) / 0.7) * 255)

        dispatch = {
            self.IDLE:       self._draw_idle,
            self.RECORDING:  self._draw_recording,
            self.PROCESSING: self._draw_processing,
            self.ERROR:      self._draw_error,
        }
        draw_fn = dispatch.get(self.state)
        if draw_fn:
            draw_fn(painter, cy, w, bar_h, alpha)

    def _draw_idle(self, painter, cy, w, bar_h, alpha):
        a = alpha
        painter.setBrush(QBrush(QColor(52, 199, 89, a)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(16, cy - 5, 10, 10)

        painter.setPen(QColor(255, 255, 255, a))
        painter.setFont(QFont("-apple-system", 13, QFont.Weight.Medium))
        painter.drawText(36, cy + 5, "Voxaho")

        hint = HOTKEY_HINT
        f2   = QFont("-apple-system", 11)
        painter.setPen(QColor(150, 150, 155, a))
        painter.setFont(f2)
        hw = QFontMetrics(f2).horizontalAdvance(hint)
        painter.drawText(w - hw - 16, cy + 5, hint)

    def _draw_recording(self, painter, cy, w, bar_h, alpha):
        a     = alpha
        pulse = 5 + int(2 * abs(math.sin(self._anim_offset * 0.5)))
        painter.setBrush(QBrush(QColor(255, 59, 48, a)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(16, cy - pulse, pulse * 2, pulse * 2)

        n_bars  = 8; bw = 4; gap = 5
        total_w = n_bars * bw + (n_bars - 1) * gap
        sx      = (w - total_w) // 2
        max_bh  = bar_h - 14

        for i, h_ratio in enumerate(self._wave_heights):
            bh       = max(4, int(h_ratio * max_bh))
            x        = sx + i * (bw + gap)
            y        = cy - bh // 2
            bar_a    = min(a, 180 + int(75 * h_ratio))
            painter.setBrush(QBrush(QColor(255, 59, 48, bar_a)))
            painter.drawRoundedRect(x, y, bw, bh, 2, 2)

        painter.setPen(QColor(255, 59, 48, a))
        painter.setFont(QFont("-apple-system", 10))
        painter.drawText(w - 50, cy + 4, "● REC")

    def _draw_processing(self, painter, cy, w, bar_h, alpha):
        a  = alpha
        n  = 3; r = 4
        sx = (w - (n * r * 2 + (n - 1) * 8)) // 2

        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            phase  = self._proc_phase + i * 1.1
            scale  = 0.4 + 0.6 * abs(math.sin(phase))
            radius = max(1, int(r * scale))
            x      = sx + i * (r * 2 + 8) + r - radius
            y      = cy - radius
            dot_a  = min(a, int(80 + 175 * abs(math.sin(phase))))
            ap = self._accent_primary
            painter.setBrush(QBrush(QColor(ap.red(), ap.green(), ap.blue(), dot_a)))
            painter.drawEllipse(x, y, radius * 2, radius * 2)

        painter.setPen(QColor(150, 150, 155, a))
        painter.setFont(QFont("-apple-system", 10))
        painter.drawText(w - 105, cy + 4, "Transcription…")

    def _draw_error(self, painter, cy, w, bar_h, alpha):
        painter.setPen(QColor(255, 69, 58, alpha))
        painter.setFont(QFont("-apple-system", 11, QFont.Weight.Medium))
        msg = "⚠  Activer Accessibilité pour Voxaho" if IS_MAC else "⚠  Erreur d'accès clavier"
        painter.drawText(
            QRect(0, 0, w, bar_h),
            Qt.AlignmentFlag.AlignCenter,
            msg,
        )

    def _draw_error_actions(self, painter: QPainter, w: int, bar_h: int):
        """Boutons d'action quand le panneau est ouvert en état ERROR (Mac)."""
        if not IS_MAC:
            return
        a = int(min(1.0, (self._expanded_frac - 0.05) / 0.95) * 220)

        painter.setPen(QPen(QColor(80, 30, 30, a), 0.5))
        painter.drawLine(20, bar_h + 2, w - 20, bar_h + 2)

        # Cache rects pour le hit-test dans mousePressEvent
        bw, bh = 150, 32
        gap = 12
        total_w = bw * 2 + gap
        x0 = (w - total_w) // 2
        y0 = bar_h + 18

        # Bouton "↻ Redémarrer" — primary
        self._error_btn_restart = QRect(x0, y0, bw, bh)
        painter.setBrush(QBrush(QColor(10, 132, 255, a)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self._error_btn_restart, 8, 8)
        painter.setPen(QColor(255, 255, 255, a))
        painter.setFont(QFont("-apple-system", 12, QFont.Weight.Bold))
        painter.drawText(self._error_btn_restart, Qt.AlignmentFlag.AlignCenter, "↻  Redémarrer")

        # Bouton "Ouvrir Réglages" — ghost
        self._error_btn_settings = QRect(x0 + bw + gap, y0, bw, bh)
        painter.setBrush(QBrush(QColor(255, 255, 255, int(0.06 * a))))
        painter.setPen(QPen(QColor(255, 255, 255, int(0.15 * a)), 1))
        painter.drawRoundedRect(self._error_btn_settings, 8, 8)
        painter.setPen(QColor(235, 235, 245, a))
        painter.setFont(QFont("-apple-system", 12, QFont.Weight.Medium))
        painter.drawText(self._error_btn_settings, Qt.AlignmentFlag.AlignCenter, "Ouvrir Réglages")

        # Texte d'aide
        painter.setPen(QColor(150, 150, 155, a))
        painter.setFont(QFont("-apple-system", 10))
        painter.drawText(
            QRect(0, y0 + bh + 12, w, 20),
            Qt.AlignmentFlag.AlignCenter,
            "1. Active Voxaho dans Réglages  2. Clique Redémarrer",
        )

    def _draw_settings_panel(self, painter: QPainter, w: int, bar_h: int):
        a = int(min(1.0, (self._expanded_frac - 0.05) / 0.95) * 220)

        painter.setPen(QPen(QColor(60, 60, 62, a), 0.5))
        painter.drawLine(20, bar_h + 2, w - 20, bar_h + 2)

        y0       = bar_h + 16
        lang_map = {"fr": "🇫🇷 Français", "en": "🇬🇧 English", "auto": "🌍 Auto"}
        rows     = [
            lang_map.get(self.config.get("language", "fr"), "🇫🇷 Français"),
            f"Modèle : {self.config.get('model', 'small')}",
            "✓ Reformatage activé" if self.config.get("reformatting") else "✗ Reformatage désactivé",
            f"⌨  Touche : {HOTKEY_LABEL}",
        ]
        f_val = QFont("-apple-system", 11)
        for i, row in enumerate(rows):
            painter.setPen(QColor(210, 210, 220, a))
            painter.setFont(f_val)
            painter.drawText(20, y0 + i * 26, row)

        # Bouton "Personnaliser…" (lien accent, à gauche)
        custom_y = y0 + len(rows) * 26 + 10
        ap = self._accent_primary
        painter.setPen(QColor(ap.red(), ap.green(), ap.blue(), a))
        painter.setFont(QFont("-apple-system", 11, QFont.Weight.Medium))
        painter.drawText(20, custom_y, "⚙  Personnaliser…")

        # Lien "Ouvrir Voxaho" (même accent, à droite sur la même ligne) — ouvre
        # la fenêtre principale Workspace (Phase 1). Géométrie via fonction pure
        # partagée avec le hit-test de mousePressEvent.
        gx, gy, _gw, _gh = open_workspace_link_geom(bar_h)
        painter.setPen(QColor(ap.red(), ap.green(), ap.blue(), a))
        painter.setFont(QFont("-apple-system", 11, QFont.Weight.Medium))
        painter.drawText(gx + 6, gy + 16, "🏠  Ouvrir Voxaho")

        painter.setPen(QColor(255, 69, 58, a))
        painter.setFont(f_val)
        painter.drawText(w - 66, y0, "Quitter ×")

    # ── Interactions souris ───────────────────────────────────────────────────

    def enterEvent(self, event):
        self._hovered = True
        self._set_target_show(1.0)
        self._set_target_expand(1.0)

    def leaveEvent(self, event):
        self._hovered = False
        self._set_target_expand(0.0)
        if self.state not in (self.RECORDING, self.PROCESSING):
            self._set_target_show(0.0)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._expanded_frac > 0.5:
                bar_h = int(self._mini_h + (BAR_H - self._mini_h) * self._show_frac)

                # En état ERROR : on a 2 boutons (Redémarrer + Ouvrir Réglages)
                if self.state == self.ERROR and IS_MAC:
                    btn_r = getattr(self, "_error_btn_restart", None)
                    btn_s = getattr(self, "_error_btn_settings", None)
                    if btn_r and btn_r.contains(event.pos()):
                        from core.relaunch import relaunch
                        relaunch()  # ne retourne jamais
                        return
                    if btn_s and btn_s.contains(event.pos()):
                        from core.relaunch import open_accessibility_settings
                        open_accessibility_settings()
                        return
                else:
                    # Quitter ×
                    quit_rect = QRect(self.width() - 80, bar_h + 2, 76, 24)
                    if quit_rect.contains(event.pos()):
                        QApplication.quit()
                        return
                    # 🏠 Ouvrir Voxaho (lien Workspace, à droite de la ligne)
                    open_rect = QRect(*open_workspace_link_geom(bar_h))
                    if open_rect.contains(event.pos()):
                        self._open_workspace()
                        return
                    # ⚙ Personnaliser…
                    custom_y = bar_h + 16 + 4 * 26 + 10
                    cust_rect = QRect(14, custom_y - 16, 170, 24)
                    if cust_rect.contains(event.pos()):
                        self._open_settings_window()
                        return
            self._drag_start = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_start and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_start
            self.move(new_pos)
            # Recalculer l'ancre lors du déplacement
            bar_h_current = int(self._mini_h + (BAR_H - self._mini_h) * self._show_frac)
            self._anchor_cx = new_pos.x() + self.width() // 2
            self._anchor_cy = new_pos.y() + bar_h_current // 2

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start:
            full_x = self._anchor_cx - BAR_W // 2
            full_y = self._anchor_cy - BAR_H // 2
            self.config["bar_x"] = full_x
            self.config["bar_y"] = full_y
            self._save_config(self.config)
            self._drag_start = None

    # ── Config à chaud ────────────────────────────────────────────────────────

    def apply_config(self, new_config: dict):
        old_win_key = self.config.get("win_key", "ctrl_r")
        old_backend = self.config.get("compute_backend", "auto")
        self.config = new_config

        new_backend = new_config.get("compute_backend", "auto")
        if new_backend != old_backend:
            # Le backend est fixé au constructeur du Transcriber : un simple
            # setattr ne suffit pas, il faut le recréer intégralement (unload de
            # l'ancien modèle, nouveau Transcriber avec la config courante, puis
            # relance du préchargement en thread daemon).
            self._recreate_transcriber()
        else:
            # Hot-swap standard : langue + reformatage + modèle (existant),
            # plus le beam_size (nouveau).
            self._transcriber.language     = None if new_config.get("language") == "auto" else new_config.get("language", "fr")
            self._transcriber.reformatting = new_config.get("reformatting", True)
            self._transcriber.update_model(new_config.get("model", "small"))
            self._apply_beam_size(new_config.get("beam_size", 1))

        # NB : input_device n'exige aucune action ici — il est stocké dans
        # self.config et lu au prochain enregistrement (_on_fn_press).

        # Touche de dictée Windows à chaud (M1) — la touche macOS (Fn) est
        # fixe, pas de reconfiguration nécessaire sur Mac
        new_win_key = new_config.get("win_key", "ctrl_r")
        if not IS_MAC and new_win_key != old_win_key:
            self._restart_hotkey(new_win_key)

        # Apparence (taille mini + accent) — répercuté sur géométrie + repaint
        self._apply_appearance(new_config)

        # Repositionnement éventuel selon bar_position
        pos = new_config.get("bar_position", "custom")
        screen = QApplication.primaryScreen().geometry()
        if pos in ("top", "bottom"):
            self._anchor_cx = screen.width() // 2
            self._anchor_cy = (64 + BAR_H // 2) if pos == "top" \
                              else (screen.height() - 64 - BAR_H // 2)

        # Re-borner la position dans l'écran courant (M5) — l'ancre peut
        # référencer un écran qui n'existe plus (moniteur débranché)
        full_x, full_y = clamp_to_screen(
            self._anchor_cx - BAR_W // 2, self._anchor_cy - BAR_H // 2,
            BAR_W, BAR_H,
            (screen.x(), screen.y(), screen.width(), screen.height()),
        )
        self._anchor_cx = full_x + BAR_W // 2
        self._anchor_cy = full_y + BAR_H // 2

        self._apply_geometry()
        self.update()

    # ── Fenêtre Préférences ───────────────────────────────────────────────────

    def _open_settings_window(self):
        try:
            from ui.settings_window import SettingsWindow
        except Exception as e:
            logger.warning(f"SettingsWindow import: {e}")
            return

        # Garder une référence (sinon GC) ; recréer si fermée
        if self._settings_win is not None:
            try:
                self._settings_win.raise_()
                self._settings_win.activateWindow()
                return
            except Exception:
                self._settings_win = None

        self._settings_win = SettingsWindow(self.config, save_config_fn=self._save_config)
        self._settings_win.settings_applied.connect(self.apply_config)
        self._settings_win.settings_preview.connect(self._on_preview)
        self._settings_win.finished.connect(self._on_settings_closed)
        self._settings_win.show()

    def _on_preview(self, preview_cfg: dict):
        """Aperçu live des changements sans toucher au config sauvegardé."""
        try:
            # NB : on ne persiste pas dans self.config — preview seulement.
            # Mais on met à jour les attributs visuels pour le rendu live.
            self._apply_appearance(preview_cfg or {})
            self._apply_geometry()
            self.update()
        except Exception as e:
            logger.warning(f"_on_preview: {e}")

    def _on_settings_closed(self, _result):
        self._settings_win = None

    # ── Fenêtre Workspace (fenêtre principale) ──────────────────────────────────

    def _open_workspace(self):
        """Ouvre (ou ré-active) la fenêtre principale Workspace (Phase 1).

        Import différé + défensif : WorkspaceWindow est fourni par un agent
        parallèle ; son indisponibilité ne doit pas casser la barre. Une seule
        instance est gardée (self._workspace_win) : si déjà ouverte, on la ramène
        au premier plan plutôt que d'en rouvrir une.
        """
        try:
            from ui.workspace_window import WorkspaceWindow
        except Exception as e:
            logger.warning(f"WorkspaceWindow import: {e}")
            return

        # Déjà ouverte → premier plan
        if self._workspace_win is not None:
            try:
                self._workspace_win.raise_()
                self._workspace_win.activateWindow()
                return
            except Exception:
                self._workspace_win = None

        try:
            self._workspace_win = WorkspaceWindow(
                self.config,
                self._save_config,
                open_settings_fn=self._open_settings_window,
            )
        except Exception as e:
            logger.warning(f"WorkspaceWindow init: {e}")
            self._workspace_win = None
            return

        # QMainWindow n'a pas de signal `finished` (contrairement à QDialog) : on
        # remet la référence à None via `destroyed` pour permettre une réouverture
        # propre après fermeture.
        try:
            self._workspace_win.destroyed.connect(self._on_workspace_closed)
        except Exception as e:
            logger.warning(f"WorkspaceWindow destroyed connect: {e}")

        self._workspace_win.show()
        try:
            self._workspace_win.raise_()
            self._workspace_win.activateWindow()
        except Exception:
            pass

    def _on_workspace_closed(self, *_args):
        self._workspace_win = None
