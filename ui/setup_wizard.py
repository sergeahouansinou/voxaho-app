"""
Wizard de premier lancement multi-pages — onboarding premium Voxaho.
S'affiche une seule fois, puis enregistre la config dans ~/.voxaho/config.json.
"""

import os
import sys
import logging
from PyQt6.QtWidgets import (
    QDialog, QStackedWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QProgressBar, QLineEdit,
    QFrame, QWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QUrl, QSize
from PyQt6.QtGui import QFont, QDesktopServices

try:
    from PyQt6.QtSvgWidgets import QSvgWidget
except ImportError:
    QSvgWidget = None

try:
    from ApplicationServices import AXIsProcessTrusted  # type: ignore
except Exception:
    AXIsProcessTrusted = None

logger = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
LOGO_PATH = os.path.join(ASSETS_DIR, "voxaho-icon.svg")

# Langues proposées : mêmes entrées (drapeaux/labels) que les Préférences —
# settings_window.LANGS est la source unique (l'import est sûr :
# settings_window n'importe pas ce module au niveau module).
from ui.settings_window import LANGS

# Threads volontairement détachés (fermeture d'un dialog pendant un
# téléchargement ou une activation) : on garde une référence module-level
# pour éviter le GC du wrapper Python ("QThread: Destroyed while thread is
# still running" → crash). Ces threads mourront avec le process.
_orphan_threads: list[QThread] = []


def _park_thread(thread: QThread) -> None:
    """Retient une référence module-level sur un QThread (anti-GC).

    Purge au passage les threads déjà terminés pour éviter que la liste
    ne grossisse indéfiniment.
    """
    _orphan_threads[:] = [t for t in _orphan_threads
                          if t is not thread and t.isRunning()]
    _orphan_threads.append(thread)

STYLESHEET = """
QDialog { background-color: #1C1C1E; }
QWidget#page { background-color: #1C1C1E; }
QLabel  { color: #FFFFFF; background: transparent; }
QLabel#subtitle { color: #8E8E93; font-size: 13px; }
QLabel#section  { color: #EBEBF5; font-size: 13px; font-weight: 600; }
QLabel#hint     { color: #636366; font-size: 11px; }
QLabel#feature  { color: #EBEBF5; font-size: 14px; }
QLabel#statusOk { color: #30D158; font-size: 13px; font-weight: 600; }
QLabel#statusWarn { color: #FF9F0A; font-size: 13px; font-weight: 600; }
QLabel#permTitle { color: #FFFFFF; font-size: 15px; font-weight: 600; }
QFrame#card {
    background-color: #2C2C2E; border-radius: 12px;
    border: 1px solid #3A3A3C;
}
QComboBox {
    background-color: #2C2C2E; color: #FFFFFF;
    border: 1px solid #3A3A3C; border-radius: 8px;
    padding: 9px 12px; font-size: 13px;
    font-family: -apple-system, "SF Pro Text", system-ui;
}
QComboBox::drop-down { border: none; padding-right: 12px; }
QComboBox QAbstractItemView {
    background-color: #2C2C2E; color: #FFFFFF;
    border: 1px solid #3A3A3C;
    selection-background-color: #0A84FF;
}
QCheckBox { color: #FFFFFF; font-size: 13px; spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 5px;
    border: 1.5px solid #636366; background: #2C2C2E;
}
QCheckBox::indicator:checked { background: #0A84FF; border-color: #0A84FF; }
QPushButton#primary {
    background-color: #0A84FF; color: #FFFFFF; border: none;
    border-radius: 10px; padding: 13px 24px;
    font-size: 15px; font-weight: 600;
    font-family: -apple-system, "SF Pro Text", system-ui;
}
QPushButton#primary:hover    { background-color: #409CFF; }
QPushButton#primary:disabled { background-color: #3A3A3C; color: #636366; }
QPushButton#ghost {
    background-color: transparent; color: #EBEBF5;
    border: 1px solid #3A3A3C; border-radius: 10px;
    padding: 11px 18px; font-size: 13px;
    font-family: -apple-system, "SF Pro Text", system-ui;
}
QPushButton#ghost:hover { border-color: #636366; color: #FFFFFF; }
QPushButton#link {
    background: transparent; color: #0A84FF; border: none;
    padding: 4px 0px; font-size: 12px; text-decoration: underline;
}
QPushButton#link:hover { color: #409CFF; }
QPushButton#skip {
    background: transparent; color: #8E8E93; border: none;
    padding: 6px 8px; font-size: 12px;
}
QPushButton#skip:hover { color: #FFFFFF; }
QProgressBar {
    background-color: #2C2C2E; border-radius: 4px;
    height: 6px; text-align: center;
    border: none;
}
QProgressBar::chunk { background-color: #0A84FF; border-radius: 4px; }
QLineEdit {
    background-color: #2C2C2E; color: #FFFFFF;
    border: 1px solid #3A3A3C; border-radius: 8px;
    padding: 9px 12px; font-size: 13px;
}
QLineEdit:focus { border-color: #0A84FF; }
QLabel#error { color: #FF453A; font-size: 12px; }
"""


# ─── Progress dots ────────────────────────────────────────────────────────────

class ProgressDots(QWidget):
    def __init__(self, total: int, parent=None):
        super().__init__(parent)
        self._total = total
        self._current = 0
        self._validated = set()
        self.setFixedHeight(14)
        self._dots = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addStretch()
        for i in range(total):
            d = QLabel()
            d.setFixedSize(8, 8)
            self._dots.append(d)
            layout.addWidget(d)
        layout.addStretch()
        self._refresh()

    def set_current(self, idx: int):
        self._current = idx
        for i in range(idx):
            self._validated.add(i)
        self._refresh()

    def _refresh(self):
        for i, d in enumerate(self._dots):
            if i == self._current:
                color = "#0A84FF"
            elif i in self._validated:
                color = "#30D158"
            else:
                color = "#3A3A3C"
            d.setStyleSheet(
                f"background-color: {color}; border-radius: 4px;"
            )


# ─── VU meter ────────────────────────────────────────────────────────────────

class VUMeter(QWidget):
    def __init__(self, bars: int = 12, parent=None):
        super().__init__(parent)
        self._bars = bars
        self._level = 0.0
        self.setFixedHeight(80)
        self.setMinimumWidth(240)

    def set_level(self, level: float):
        self._level = max(0.0, min(1.0, level))
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        bar_w = (w - (self._bars - 1) * 6) / self._bars
        active = int(self._level * self._bars)
        for i in range(self._bars):
            x = int(i * (bar_w + 6))
            ratio = i / max(1, self._bars - 1)
            if i < active:
                if ratio < 0.5:
                    color = QColor("#30D158")
                elif ratio < 0.8:
                    color = QColor("#FFD60A")
                else:
                    color = QColor("#FF453A")
            else:
                color = QColor("#3A3A3C")
            bar_h = int(h * (0.25 + 0.75 * ratio))
            y = h - bar_h
            p.fillRect(int(x), y, int(bar_w), bar_h, color)
        p.end()


# ─── Model downloader (inchangé) ──────────────────────────────────────────────

class ModelDownloader(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, model_name: str):
        super().__init__()
        self.model_name = model_name

    def run(self):
        try:
            self.progress.emit(20)
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise RuntimeError(
                    "faster-whisper n'est pas installé.\n"
                    "Lancez d'abord : ./install.sh"
                )
            self.progress.emit(50)
            WhisperModel(self.model_name, device="cpu", compute_type="int8")
            self.progress.emit(100)
            self.finished.emit()
        except Exception as e:
            logger.error(f"ModelDownloader: {e}", exc_info=True)
            self.error.emit(str(e))


# ─── Activation de licence en arrière-plan ────────────────────────────────────

class _ActivationWorker(QThread):
    """Exécute core.license.activate(key) hors du thread GUI.

    L'activation fait un appel réseau urllib (timeout 10 s) qui gelait
    l'interface. Le résultat remonte par signaux : l'émission est
    thread-safe et Qt replanifie les slots sur le thread du receveur
    (queued connection). Si le dialog receveur est détruit pendant
    l'appel, Qt coupe les connexions — le worker ne crashe pas ; il
    suffit de le garder référencé via _park_thread().

    Réutilisé par SettingsWindow (ui/settings_window.py, import différé).
    """

    success = pyqtSignal()
    error   = pyqtSignal(str)

    def __init__(self, key: str, parent=None):
        # parent=None par défaut : le worker doit survivre à la destruction
        # du dialog appelant (référence anti-GC via _park_thread).
        super().__init__(parent)
        self._key = key

    def run(self):
        from core import license as license_mod
        try:
            license_mod.activate(self._key)
        except license_mod.LicenseError as e:
            self.error.emit(str(e))
        except Exception as e:
            logger.error(f"_ActivationWorker: {e}", exc_info=True)
            self.error.emit(f"Erreur inattendue : {e}")
        else:
            self.success.emit()


# ─── Dictée d'essai (enregistrement + transcription hors thread GUI) ──────────

class _TrialDictationWorker(QThread):
    """Enregistre ~N secondes puis transcrit, entièrement hors du thread GUI.

    Le thread GUI ne doit JAMAIS geler : l'enregistrement (bloquant) ET la
    transcription (chargement modèle + inférence) se font ici. Les résultats
    remontent par signaux (queued connection, thread-safe).

    100 % défensif : toute erreur (dépendance absente, modèle non prêt, aucun
    son…) est émise via `error` — l'appelant affiche alors un message informatif
    sans casser le wizard. N'a pas de parent (survit à la destruction du dialog,
    référence anti-GC via _park_thread).
    """

    transcribing = pyqtSignal()      # audio capturé, transcription démarrée
    finished_text = pyqtSignal(str)  # texte transcrit (éventuellement vide)
    error = pyqtSignal(str)          # échec → essai indisponible

    def __init__(self, *, model: str, language: str, beam_size: int,
                 device=None, seconds: float = 3.0, parent=None):
        super().__init__(parent)
        self._model    = model
        self._language = language
        self._beam     = beam_size
        self._device   = device
        self._seconds  = seconds

    def _record(self):
        """Capture ~seconds d'audio float32 mono 16 kHz. Renvoie np.ndarray|None.

        Utilise le Recorder du moteur si importable (respecte le device choisi),
        sinon repli direct sur sounddevice.
        """
        import time
        # Voie privilégiée : Recorder existant (même chemin que la dictée réelle).
        try:
            from core.recorder import Recorder
            rec = Recorder(device=self._device)
            rec.start()
            time.sleep(self._seconds + 0.2)
            return rec.stop()
        except Exception as e:
            logger.debug(f"Recorder indisponible, repli sounddevice : {e}")
        # Repli : sounddevice direct.
        import sounddevice as sd
        import numpy as np
        frames = int(16000 * self._seconds)
        audio = sd.rec(frames, samplerate=16000, channels=1,
                       dtype="float32", device=self._device)
        sd.wait()
        return np.asarray(audio, dtype="float32").flatten()

    def run(self):
        try:
            audio = self._record()
            if audio is None or len(audio) == 0:
                self.finished_text.emit("")  # pas de son → texte vide (non bloquant)
                return
            self.transcribing.emit()
            from core.transcriber import Transcriber
            tr = Transcriber(
                model=self._model, language=self._language,
                reformatting=True, beam_size=self._beam,
            )
            text = tr.transcribe(audio)
            self.finished_text.emit(text or "")
        except Exception as e:
            logger.debug(f"_TrialDictationWorker: {e}", exc_info=True)
            self.error.emit(str(e))


# ─── Wizard ───────────────────────────────────────────────────────────────────

class SetupWizard(QDialog):
    """Wizard multi-pages.

    Pages: 0 Welcome, 1 Permissions (mac), 2 Config, 3 Download,
           4 Mic test, 5 Tutorial, 6 Final.
    Sur Windows, page 1 est sautée.
    """

    # Niveau micro relayé du thread audio (callback PortAudio) vers le thread
    # GUI : l'émission d'un signal est thread-safe, Qt fait la queued connection.
    _vu_level = pyqtSignal(float)

    def __init__(self, save_config_fn):
        super().__init__()
        self._save_config = save_config_fn
        self._downloader: ModelDownloader | None = None
        self._config_draft: dict = {
            "language":        "fr",
            "model":           "small",
            "reformatting":    True,
            "first_run":       False,
            "bar_x":           None,
            "bar_y":           None,
            # Réglages vitesse (Phase 0) : cohérence avec les Préférences.
            # beam_size 1 = « Vitesse » ; input_device None = micro système.
            "beam_size":       1,
            "input_device":    None,
            # Backend de calcul (Phase 3b) : issu de la reco matérielle
            # ("auto"|"cpu"|"mlx"). "auto" retombe sur CPU si MLX absent.
            "compute_backend": "auto",
            # Reformatage par IA locale (Qwen) : préférence mémorisée seulement,
            # le modèle (~1 Go) sera téléchargé plus tard depuis les Réglages.
            "ai_reformat":     False,
        }
        # Détection matérielle + recommandation de modèle (défensif : si le
        # module core.hardware est absent ou échoue, on garde small par défaut).
        self._hw: dict = {}
        self._reco: dict | None = None
        self._detect_hardware()
        self._mic_timer: QTimer | None = None
        self._mic_stream = None
        self._mic_samples = []
        self._mic_seconds_left = 0
        # Worker de dictée d'essai (page Mic test), gardé référencé anti-GC.
        self._trial_worker = None
        self._setup_ui()

    # ── Détection matérielle ───────────────────────────────────────────────────

    def _detect_hardware(self):
        """Détecte le matériel et calcule la reco de modèle (100 % défensif).

        Toute erreur (module absent, exception) → self._hw = {} et
        self._reco = None : le wizard retombe alors sur son comportement
        d'origine (modèle « small » par défaut).
        """
        try:
            from core import hardware
            self._hw = hardware.detect_hardware()
            self._reco = hardware.recommend_model(self._hw)
        except Exception as e:
            logger.debug(f"Détection matérielle indisponible : {e}")
            self._hw = {}
            self._reco = None

    # ── Construction ──────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle("Voxaho — Configuration")
        self.setFixedSize(580, 680)
        self.setStyleSheet(STYLESHEET)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.MSWindowsFixedSizeDialogHint
        )

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(36, 28, 36, 24)

        self._dots = ProgressDots(total=7)
        root.addWidget(self._dots)
        root.addSpacing(20)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # Pages
        self._stack.addWidget(self._build_welcome())      # 0
        self._stack.addWidget(self._build_permissions())  # 1
        self._stack.addWidget(self._build_config())       # 2
        self._stack.addWidget(self._build_download())     # 3
        self._stack.addWidget(self._build_mic_test())     # 4
        self._stack.addWidget(self._build_tutorial())     # 5
        self._stack.addWidget(self._build_final())        # 6

        root.addSpacing(16)

        # Nav bar
        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(10)

        self._btn_skip = QPushButton("Passer")
        self._btn_skip.setObjectName("skip")
        self._btn_skip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_skip.clicked.connect(self._on_skip)
        nav.addWidget(self._btn_skip)

        nav.addStretch()

        self._btn_back = QPushButton("← Retour")
        self._btn_back.setObjectName("ghost")
        self._btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_back.clicked.connect(self._on_back)
        nav.addWidget(self._btn_back)

        self._btn_next = QPushButton("Continuer  →")
        self._btn_next.setObjectName("primary")
        self._btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_next.clicked.connect(self._on_next)
        nav.addWidget(self._btn_next)

        root.addLayout(nav)

        self._go_to(0)

    # ── Page builders ─────────────────────────────────────────────────────────

    def _page_widget(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        w.setObjectName("page")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        return w, lay

    def _build_welcome(self) -> QWidget:
        page, lay = self._page_widget()
        lay.addStretch()

        if QSvgWidget is not None and os.path.exists(LOGO_PATH):
            logo = QSvgWidget(LOGO_PATH)
            logo.setFixedSize(96, 96)
            holder = QHBoxLayout()
            holder.addStretch()
            holder.addWidget(logo)
            holder.addStretch()
            lay.addLayout(holder)
        lay.addSpacing(20)

        title = QLabel("Bienvenue dans Voxaho")
        title.setFont(QFont("-apple-system", 26, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(8)

        sub = QLabel("Dictez. Voxaho écrit pour vous.")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(32)

        for icon, text in [
            ("🎙", "Dictée vocale instantanée"),
            ("🔒", "100 % local, aucune donnée envoyée"),
            ("⚡", "Compatible avec toutes vos apps"),
        ]:
            row = QHBoxLayout()
            row.addStretch()
            i = QLabel(icon)
            i.setFont(QFont("-apple-system", 20))
            row.addWidget(i)
            row.addSpacing(12)
            t = QLabel(text)
            t.setObjectName("feature")
            row.addWidget(t)
            row.addStretch()
            lay.addLayout(row)
            lay.addSpacing(14)

        lay.addStretch()
        return page

    def _build_permissions(self) -> QWidget:
        page, lay = self._page_widget()

        title = QLabel("Autoriser Voxaho")
        title.setFont(QFont("-apple-system", 22, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(6)

        sub = QLabel("Voxaho a besoin de 2 permissions système pour fonctionner")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(28)

        # ── Microphone ────────────────────────────────────
        mic_card = QFrame(); mic_card.setObjectName("card")
        mc = QVBoxLayout(mic_card); mc.setContentsMargins(18, 16, 18, 16); mc.setSpacing(8)
        head = QHBoxLayout()
        h = QLabel("🎙  Microphone"); h.setObjectName("permTitle")
        head.addWidget(h)
        head.addStretch()
        self._mic_status = QLabel("⚠ En attente")
        self._mic_status.setObjectName("statusWarn")
        head.addWidget(self._mic_status)
        mc.addLayout(head)
        mc_hint = QLabel("Pour enregistrer votre voix.")
        mc_hint.setObjectName("hint")
        mc.addWidget(mc_hint)
        mic_btn = QPushButton("Ouvrir les Réglages → Microphone")
        mic_btn.setObjectName("ghost")
        mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mic_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        )))
        mc.addWidget(mic_btn)
        lay.addWidget(mic_card)
        lay.addSpacing(14)

        # ── Accessibilité ────────────────────────────────
        ax_card = QFrame(); ax_card.setObjectName("card")
        ac = QVBoxLayout(ax_card); ac.setContentsMargins(18, 16, 18, 16); ac.setSpacing(8)
        head2 = QHBoxLayout()
        h2 = QLabel("⌨  Accessibilité"); h2.setObjectName("permTitle")
        head2.addWidget(h2)
        head2.addStretch()
        self._ax_status = QLabel("⚠ Non autorisé")
        self._ax_status.setObjectName("statusWarn")
        head2.addWidget(self._ax_status)
        ac.addLayout(head2)
        ax_hint = QLabel("Pour intercepter la touche Fn. Cochez Voxaho dans la liste après ouverture.")
        ax_hint.setObjectName("hint")
        ax_hint.setWordWrap(True)
        ac.addWidget(ax_hint)
        ax_btn = QPushButton("Ouvrir les Réglages → Accessibilité")
        ax_btn.setObjectName("ghost")
        ax_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ax_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        )))
        ac.addWidget(ax_btn)
        lay.addWidget(ax_card)
        lay.addSpacing(16)

        # ── Recheck row ─────────────────────────────────
        recheck_row = QHBoxLayout()
        self._btn_recheck = QPushButton("Vérifier à nouveau")
        self._btn_recheck.setObjectName("ghost")
        self._btn_recheck.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_recheck.clicked.connect(self._check_permissions)
        recheck_row.addStretch()
        recheck_row.addWidget(self._btn_recheck)
        recheck_row.addStretch()
        lay.addLayout(recheck_row)
        lay.addSpacing(10)

        self._perm_warn = QPushButton("Continuer quand même")
        self._perm_warn.setObjectName("link")
        self._perm_warn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._perm_warn.clicked.connect(self._force_perm_continue)
        self._perm_warn.hide()
        warn_row = QHBoxLayout()
        warn_row.addStretch()
        warn_row.addWidget(self._perm_warn)
        warn_row.addStretch()
        lay.addLayout(warn_row)

        lay.addStretch()
        return page

    def _build_config(self) -> QWidget:
        page, lay = self._page_widget()

        title = QLabel("Configuration")
        title.setFont(QFont("-apple-system", 22, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(6)

        sub = QLabel("Choisissez votre langue et le modèle Whisper")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(18)

        # ── Résumé matériel + recommandation ─────────────────────────────
        # Affiché seulement si la détection a réussi ; sinon on saute cette
        # carte et le comportement d'origine (small par défaut) est conservé.
        if self._reco is not None:
            hw_card = QFrame(); hw_card.setObjectName("card")
            hc = QVBoxLayout(hw_card)
            hc.setContentsMargins(16, 12, 16, 12); hc.setSpacing(4)
            hw_head = QLabel("💻  Votre machine"); hw_head.setObjectName("permTitle")
            hc.addWidget(hw_head)
            try:
                from core import hardware
                specs = hardware.specs_summary(self._hw)
            except Exception:
                specs = ""
            if specs:
                specs_lbl = QLabel(specs); specs_lbl.setObjectName("subtitle")
                hc.addWidget(specs_lbl)
            reco_lbl = QLabel(f"Recommandé : {self._reco['model']}")
            reco_lbl.setObjectName("statusOk")
            hc.addWidget(reco_lbl)
            reason_lbl = QLabel(self._reco.get("reason", ""))
            reason_lbl.setObjectName("hint"); reason_lbl.setWordWrap(True)
            hc.addWidget(reason_lbl)
            lay.addWidget(hw_card)
            lay.addSpacing(16)

        lbl_lang = QLabel("Langue de dictée"); lbl_lang.setObjectName("section")
        lay.addWidget(lbl_lang)
        lay.addSpacing(6)
        self.lang_combo = QComboBox()
        for code, label in LANGS:
            self.lang_combo.addItem(label, code)
        lay.addWidget(self.lang_combo)
        lay.addSpacing(16)

        lbl_model = QLabel("Modèle Whisper (précision / vitesse)")
        lbl_model.setObjectName("section")
        lay.addWidget(lbl_model)
        lay.addSpacing(4)
        perf_hint = "Sur M5 : 'small' ≈ 0,8 s · 'large-v3-turbo' ≈ 1 s · 'large-v3' ≈ 4 s" if IS_MAC else "'large-v3-turbo' recommandé (quasi-max, rapide) · 'small' plus léger"
        hint_model = QLabel(perf_hint); hint_model.setObjectName("hint")
        lay.addWidget(hint_model)
        lay.addSpacing(6)
        # Liste alignée sur settings_window.MODELS (mêmes codes/currentData).
        self.model_combo = QComboBox()
        self.model_combo.addItem("tiny            — Ultra rapide (moins précis)",       "tiny")
        self.model_combo.addItem("small           — Léger et rapide",                   "small")
        self.model_combo.addItem("medium          — Très précis",                       "medium")
        self.model_combo.addItem("large-v3-turbo  — Quasi-max, très rapide  ✓ Recommandé", "large-v3-turbo")
        self.model_combo.addItem("large-v3        — Meilleure qualité",                 "large-v3")
        # Pré-sélection du modèle recommandé (défaut small si pas de reco).
        self._select_recommended_model()
        lay.addWidget(self.model_combo)
        lay.addSpacing(4)

        # Bouton discret pour re-sélectionner la reco (visible si reco dispo).
        if self._reco is not None:
            reco_row = QHBoxLayout()
            reco_row.addStretch()
            self._btn_use_reco = QPushButton("Utiliser la recommandation")
            self._btn_use_reco.setObjectName("link")
            self._btn_use_reco.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn_use_reco.clicked.connect(self._select_recommended_model)
            reco_row.addWidget(self._btn_use_reco)
            reco_row.addStretch()
            lay.addLayout(reco_row)
        lay.addSpacing(14)

        self.reform_check = QCheckBox(
            "Reformatage IA  (supprime 'euh', ponctuation automatique)"
        )
        self.reform_check.setChecked(True)
        lay.addWidget(self.reform_check)
        lay.addSpacing(10)

        # Option informative : reformatage par IA locale (Qwen). NE déclenche
        # AUCUN téléchargement ici — on mémorise seulement la préférence
        # (ai_reformat) ; le modèle (~1 Go) se télécharge depuis les Réglages.
        self.ai_reform_check = QCheckBox(
            "Reformatage par IA locale (Qwen, ~1 Go) — télécharger plus tard dans les Réglages"
        )
        self.ai_reform_check.setChecked(bool(self._config_draft.get("ai_reformat", False)))
        lay.addWidget(self.ai_reform_check)
        lay.addSpacing(14)

        self._win_key_combo = None
        if not IS_MAC:
            lbl_key = QLabel("Touche déclencheur"); lbl_key.setObjectName("section")
            lay.addWidget(lbl_key)
            lay.addSpacing(4)
            hint_key = QLabel("Maintenez cette touche pour dicter")
            hint_key.setObjectName("hint")
            lay.addWidget(hint_key)
            lay.addSpacing(6)
            self._win_key_combo = QComboBox()
            self._win_key_combo.addItem("Ctrl Droite  ✓ Recommandé", "ctrl_r")
            self._win_key_combo.addItem("Ctrl Gauche",                "ctrl_l")
            self._win_key_combo.addItem("Alt Droite",                 "alt_r")
            self._win_key_combo.addItem("Maj Droite",                 "shift_r")
            self._win_key_combo.addItem("Verr Maj",                   "caps_lock")
            lay.addWidget(self._win_key_combo)

        lay.addStretch()
        return page

    def _select_recommended_model(self):
        """(Ré)aligne le combo modèle sur la recommandation matérielle.

        Sans reco (détection indisponible) → défaut « small » (comportement
        d'origine). Défensif : si le code recommandé n'est pas dans le combo,
        on retombe sur small.
        """
        target = self._reco.get("model") if self._reco else "small"
        idx = self.model_combo.findData(target)
        if idx < 0:
            idx = self.model_combo.findData("small")
        self.model_combo.setCurrentIndex(idx if idx >= 0 else 1)

    def _build_download(self) -> QWidget:
        page, lay = self._page_widget()
        lay.addStretch()

        title = QLabel("Téléchargement du modèle")
        title.setFont(QFont("-apple-system", 22, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(8)

        self._dl_label = QLabel("Préparation…")
        self._dl_label.setObjectName("subtitle")
        self._dl_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._dl_label)
        lay.addSpacing(28)

        self._dl_progress = QProgressBar()
        self._dl_progress.setRange(0, 100)
        self._dl_progress.setValue(0)
        lay.addWidget(self._dl_progress)
        lay.addSpacing(16)

        self._dl_hint = QLabel("Cela peut prendre quelques minutes selon votre connexion.")
        self._dl_hint.setObjectName("hint")
        self._dl_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._dl_hint)

        lay.addStretch()
        return page

    def _build_mic_test(self) -> QWidget:
        page, lay = self._page_widget()

        title = QLabel("Testons votre microphone")
        title.setFont(QFont("-apple-system", 22, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(6)

        sub = QLabel("Choisissez un micro, testez le niveau, puis dictez pour de vrai")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addSpacing(16)

        # ── Sélection du micro ────────────────────────────────────────────
        lbl_mic = QLabel("Microphone"); lbl_mic.setObjectName("section")
        lay.addWidget(lbl_mic)
        lay.addSpacing(6)
        self._mic_combo = QComboBox()
        self._populate_mic_combo()
        lay.addWidget(self._mic_combo)
        lay.addSpacing(16)

        meter_row = QHBoxLayout()
        meter_row.addStretch()
        self._vu = VUMeter(bars=12)
        # Le signal est émis depuis le thread audio : Qt replanifie
        # automatiquement set_level sur le thread GUI (queued connection).
        self._vu_level.connect(self._vu.set_level)
        meter_row.addWidget(self._vu)
        meter_row.addStretch()
        lay.addLayout(meter_row)
        lay.addSpacing(12)

        self._mic_result = QLabel("")
        self._mic_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mic_result.setObjectName("subtitle")
        lay.addWidget(self._mic_result)
        lay.addSpacing(14)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_mic = QPushButton("Commencer le test")
        self._btn_mic.setObjectName("ghost")
        self._btn_mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_mic.clicked.connect(self._start_mic_test)
        btn_row.addWidget(self._btn_mic)

        # Dictée d'essai réelle (bonus, best-effort). Enregistre ~3 s, transcrit
        # dans un QThread (jamais sur le thread GUI) et affiche le texte obtenu.
        self._btn_trial = QPushButton("Faire une dictée d'essai")
        self._btn_trial.setObjectName("ghost")
        self._btn_trial.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_trial.clicked.connect(self._start_trial_dictation)
        btn_row.addWidget(self._btn_trial)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        lay.addSpacing(12)

        self._trial_field = QLineEdit()
        self._trial_field.setReadOnly(True)
        self._trial_field.setPlaceholderText("Le texte de votre dictée d'essai apparaîtra ici")
        lay.addWidget(self._trial_field)
        lay.addSpacing(4)

        self._trial_status = QLabel("")
        self._trial_status.setObjectName("hint")
        self._trial_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._trial_status.setWordWrap(True)
        lay.addWidget(self._trial_status)

        lay.addStretch()
        return page

    def _populate_mic_combo(self):
        """Peuple le combo de micros (défensif : agent moteur peut être absent).

        1ʳᵉ entrée « Micro système par défaut » = None (currentData), puis chaque
        périphérique d'entrée (index sounddevice en currentData). Si le helper
        est indisponible ou renvoie une liste vide, seule l'entrée par défaut
        reste et le combo est désactivé.
        """
        self._mic_combo.clear()
        self._mic_combo.addItem("Micro système par défaut", None)
        devices = []
        try:
            from core.recorder import list_input_devices
            devices = list_input_devices() or []
        except Exception as e:  # ImportError, erreur PortAudio, etc.
            logger.debug(f"list_input_devices indisponible : {e}")
            devices = []
        if devices:
            self._mic_combo.setEnabled(True)
            for dev in devices:
                try:
                    idx = int(dev["index"])
                    name = str(dev.get("name", f"Périphérique {idx}"))
                    is_default = bool(dev.get("default", False))
                except (KeyError, TypeError, ValueError):
                    continue
                label = f"{name}  ✓ défaut système" if is_default else name
                self._mic_combo.addItem(label, idx)
        else:
            self._mic_combo.setEnabled(False)
        # Restaure la préférence éventuelle du brouillon.
        current = self._config_draft.get("input_device")
        if current is not None:
            pos = self._mic_combo.findData(current)
            if pos >= 0:
                self._mic_combo.setCurrentIndex(pos)

    def _selected_input_device(self):
        """Index du micro sélectionné dans le wizard, ou None (défaut système)."""
        combo = getattr(self, "_mic_combo", None)
        if combo is None:
            return None
        return combo.currentData()

    def _build_tutorial(self) -> QWidget:
        page, lay = self._page_widget()

        title = QLabel("Comment dicter avec Voxaho")
        title.setFont(QFont("-apple-system", 22, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(8)

        key_name = "Fn" if IS_MAC else "Ctrl Droit"
        sub = QLabel(f"En 4 étapes simples")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(28)

        steps = [
            ("1", "Cliquez où vous voulez écrire (email, document, code…)"),
            ("2", f"Maintenez {key_name} appuyée"),
            ("3", "Parlez naturellement"),
            ("4", "Relâchez — le texte apparaît"),
        ]
        for num, txt in steps:
            row = QHBoxLayout()
            num_lbl = QLabel(num)
            num_lbl.setFixedSize(28, 28)
            num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num_lbl.setStyleSheet(
                "background-color: #0A84FF; color: white; "
                "border-radius: 14px; font-weight: 700; font-size: 13px;"
            )
            row.addWidget(num_lbl)
            row.addSpacing(14)
            t = QLabel(txt); t.setObjectName("feature"); t.setWordWrap(True)
            row.addWidget(t, 1)
            lay.addLayout(row)
            lay.addSpacing(12)

        lay.addSpacing(10)
        tip = QLabel("💡  La barre flottante en bas d'écran indique l'état de Voxaho")
        tip.setObjectName("hint")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setWordWrap(True)
        lay.addWidget(tip)

        lay.addStretch()
        return page

    def _build_final(self) -> QWidget:
        page, lay = self._page_widget()
        lay.addStretch()

        check = QLabel("✓")
        check.setStyleSheet("color: #30D158; font-size: 56px;")
        check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(check)
        lay.addSpacing(12)

        title = QLabel("Tout est prêt !")
        title.setFont(QFont("-apple-system", 26, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(10)

        sub = QLabel(
            "Voxaho est configuré. La barre flottante apparaîtra en bas de votre écran."
        )
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addSpacing(24)

        self._final_key = QLabel("")
        self._final_key.setObjectName("feature")
        self._final_key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._final_key)
        lay.addSpacing(14)

        link = QLabel("Personnaliser plus tard via la barre → Personnaliser")
        link.setObjectName("hint")
        link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(link)

        lay.addStretch()
        return page

    # ── Navigation ────────────────────────────────────────────────────────────

    def _visible_index(self, idx: int) -> int:
        """Skip page 1 (permissions) on non-Mac."""
        if not IS_MAC and idx == 1:
            return 2
        return idx

    def _go_to(self, idx: int):
        idx = self._visible_index(idx)
        if idx < 0:
            idx = 0
        if idx > 6:
            idx = 6
        self._stack.setCurrentIndex(idx)
        self._dots.set_current(idx)
        self._update_nav(idx)

        if idx == 1:
            self._check_permissions()
        elif idx == 3:
            self._start_download()
        elif idx == 6:
            self._finalize_config_text()

    def _update_nav(self, idx: int):
        self._btn_back.setVisible(idx > 0)
        # Skip button hidden on permissions, download, final
        self._btn_skip.setVisible(idx not in (1, 3, 6))

        if idx == 0:
            self._btn_next.setText("Commencer  →")
            self._btn_next.setEnabled(True)
        elif idx == 1:
            self._btn_next.setText("Continuer  →")
            # Enabled state managed by _check_permissions
        elif idx == 2:
            self._btn_next.setText("Continuer  →")
            self._btn_next.setEnabled(True)
        elif idx == 3:
            self._btn_next.setText("Téléchargement…")
            self._btn_next.setEnabled(False)
            self._btn_back.setVisible(False)
        elif idx == 4:
            self._btn_next.setText("Continuer  →")
            self._btn_next.setEnabled(True)
        elif idx == 5:
            self._btn_next.setText("C'est parti  →")
            self._btn_next.setEnabled(True)
        elif idx == 6:
            self._btn_next.setText("Lancer Voxaho")
            self._btn_next.setEnabled(True)

    def _on_next(self):
        cur = self._stack.currentIndex()
        if cur == 2:
            # snapshot config draft
            self._config_draft["language"]     = self.lang_combo.currentData()
            self._config_draft["model"]        = self.model_combo.currentData()
            self._config_draft["reformatting"] = self.reform_check.isChecked()
            self._config_draft["ai_reformat"]  = self.ai_reform_check.isChecked()
            # beam_size + compute_backend proviennent de la reco matérielle
            # (défauts déjà présents dans _config_draft si pas de reco).
            if self._reco is not None:
                self._config_draft["beam_size"]       = int(self._reco.get("beam_size", 1))
                self._config_draft["compute_backend"] = self._reco.get("compute_backend", "auto")
            if self._win_key_combo is not None:
                self._config_draft["win_key"] = self._win_key_combo.currentData()
        if cur == 4:
            # Snapshot du micro choisi (page Mic test) : index périphérique
            # sounddevice ou None (« Micro système par défaut »).
            if getattr(self, "_mic_combo", None) is not None:
                self._config_draft["input_device"] = self._mic_combo.currentData()
        if cur == 6:
            # Save and accept
            self._save_config(self._config_draft)
            self.accept()
            return
        self._go_to(cur + 1)

    def _on_back(self):
        cur = self._stack.currentIndex()
        target = cur - 1
        if not IS_MAC and target == 1:
            target = 0
        self._go_to(target)

    def _on_skip(self):
        # Skip = go to next, same as next without snapshot
        self._on_next()

    # ── Permissions ──────────────────────────────────────────────────────────

    def _check_microphone(self) -> bool:
        try:
            import sounddevice as sd
            with sd.InputStream(channels=1, samplerate=16000, blocksize=256):
                pass
            return True
        except Exception as e:
            logger.debug(f"mic check failed: {e}")
            return False

    def _check_accessibility(self) -> bool:
        if AXIsProcessTrusted is None:
            return False
        try:
            return bool(AXIsProcessTrusted())
        except Exception:
            return False

    def _check_permissions(self):
        mic_ok = self._check_microphone()
        ax_ok = self._check_accessibility() if IS_MAC else True

        if mic_ok:
            self._mic_status.setText("✓ Autorisé")
            self._mic_status.setObjectName("statusOk")
        else:
            self._mic_status.setText("⚠ En attente")
            self._mic_status.setObjectName("statusWarn")
        self._mic_status.setStyle(self._mic_status.style())
        self._mic_status.style().unpolish(self._mic_status)
        self._mic_status.style().polish(self._mic_status)

        if ax_ok:
            self._ax_status.setText("✓ Autorisé")
            self._ax_status.setObjectName("statusOk")
        else:
            self._ax_status.setText("⚠ Non autorisé")
            self._ax_status.setObjectName("statusWarn")
        self._ax_status.style().unpolish(self._ax_status)
        self._ax_status.style().polish(self._ax_status)

        all_ok = mic_ok and ax_ok
        self._btn_next.setEnabled(all_ok)
        self._perm_warn.setVisible(not all_ok)

    def _force_perm_continue(self):
        self._btn_next.setEnabled(True)

    # ── Download ─────────────────────────────────────────────────────────────

    def _start_download(self):
        if self._downloader and self._downloader.isRunning():
            return
        model = self._config_draft.get("model", "small")
        self._dl_label.setText(f"Téléchargement du modèle « {model} »…")
        self._dl_progress.setValue(5)
        self._downloader = ModelDownloader(model)
        self._downloader.progress.connect(self._dl_progress.setValue)
        self._downloader.finished.connect(self._on_download_done)
        self._downloader.error.connect(self._on_download_error)
        self._downloader.start()

    def _on_download_done(self):
        self._dl_label.setText("✓ Modèle prêt")
        QTimer.singleShot(400, lambda: self._go_to(4))

    def _on_download_error(self, msg: str):
        if "timeout" in msg.lower() or "connection" in msg.lower() or "network" in msg.lower():
            friendly = "Erreur réseau. Vérifiez votre connexion et réessayez."
        elif "space" in msg.lower() or "disk" in msg.lower():
            friendly = "Espace disque insuffisant. Libérez 500 Mo et réessayez."
        elif "not installed" in msg.lower() or "ImportError" in msg:
            friendly = "faster-whisper manquant. Relancez ./install.sh d'abord."
        else:
            friendly = f"Erreur : {msg}"
        self._dl_label.setText(friendly)
        self._btn_next.setText("Réessayer  →")
        self._btn_next.setEnabled(True)
        try:
            self._btn_next.clicked.disconnect()
        except Exception:
            pass
        self._btn_next.clicked.connect(self._retry_download)
        self._btn_back.setVisible(True)

    def _retry_download(self):
        try:
            self._btn_next.clicked.disconnect()
        except Exception:
            pass
        self._btn_next.clicked.connect(self._on_next)
        self._start_download()
        self._update_nav(3)

    # ── Mic test ─────────────────────────────────────────────────────────────

    def _start_mic_test(self):
        try:
            import sounddevice as sd
            import numpy as np
        except Exception as e:
            self._mic_result.setText(f"⚠ sounddevice indisponible : {e}")
            return

        self._btn_mic.setEnabled(False)
        self._btn_mic.setText("Enregistrement…")
        self._mic_result.setText("")
        self._mic_samples = []
        self._mic_seconds_left = 3

        def cb(indata, frames, time_info, status):
            try:
                import numpy as np
                level = float(np.sqrt(np.mean(indata.astype("float32") ** 2)))
                # stocké pour le calcul final en dB
                self._mic_samples.append(level)
                # Émission de signal thread-safe vers le thread GUI.
                # (QTimer.singleShot depuis un thread non-Qt est interdit :
                # "Timers can only be used with threads started with QThread".)
                self._vu_level.emit(min(1.0, level * 6.0))
            except Exception:
                pass

        try:
            self._mic_stream = sd.InputStream(
                channels=1, samplerate=16000, blocksize=512, callback=cb,
                dtype="float32", device=self._selected_input_device(),
            )
            self._mic_stream.start()
        except Exception as e:
            self._mic_result.setText(f"⚠ Impossible d'ouvrir le micro : {e}")
            self._btn_mic.setEnabled(True)
            self._btn_mic.setText("Commencer le test")
            return

        self._mic_timer = QTimer(self)
        self._mic_timer.setInterval(1000)
        self._mic_timer.timeout.connect(self._mic_tick)
        self._mic_timer.start()

    def _mic_tick(self):
        self._mic_seconds_left -= 1
        if self._mic_seconds_left <= 0:
            self._stop_mic_test()

    def _stop_mic_test(self):
        if self._mic_timer:
            self._mic_timer.stop()
            self._mic_timer = None
        try:
            if self._mic_stream is not None:
                self._mic_stream.stop()
                self._mic_stream.close()
        except Exception:
            pass
        self._mic_stream = None
        self._vu.set_level(0)
        self._btn_mic.setEnabled(True)
        self._btn_mic.setText("Refaire le test")

        if not self._mic_samples:
            self._mic_result.setText("⚠ Pas de son détecté — vérifiez votre micro")
            return
        import math
        avg = sum(self._mic_samples) / len(self._mic_samples)
        if avg < 0.005:
            self._mic_result.setText("⚠ Pas de son détecté — vérifiez votre micro")
        else:
            db = 20 * math.log10(max(avg, 1e-6))
            self._mic_result.setText(f"✓ Micro OK · niveau moyen {db:.0f} dB")

    # ── Dictée d'essai réelle (bonus, best-effort) ────────────────────────────

    def _start_trial_dictation(self):
        """Lance une courte dictée d'essai : ~3 s d'audio → transcription.

        100 % DÉFENSIF ET OPTIONNEL. L'enregistrement ET la transcription
        tournent dans un QThread (_TrialDictationWorker) : le thread GUI n'est
        JAMAIS bloqué. Tout échec (modèle non prêt, dépendance absente, aucun
        son…) affiche un message informatif sans casser le wizard.
        """
        # Un test de niveau en cours ? On l'arrête d'abord (partage du micro).
        if self._mic_stream is not None:
            self._stop_mic_test()
        # Déjà une dictée d'essai en cours ?
        if self._trial_worker is not None and self._trial_worker.isRunning():
            return

        self._btn_trial.setEnabled(False)
        self._btn_trial.setText("Écoutez… parlez !")
        self._trial_field.clear()
        self._trial_status.setText("Enregistrement de 3 secondes…")

        model    = self._config_draft.get("model", "small")
        language = self._config_draft.get("language", "fr")
        beam     = int(self._config_draft.get("beam_size", 1))
        device   = self._selected_input_device()

        worker = _TrialDictationWorker(
            model=model, language=language, beam_size=beam,
            device=device, seconds=3.0,
        )
        self._trial_worker = worker
        _park_thread(worker)
        worker.transcribing.connect(self._on_trial_transcribing)
        worker.finished_text.connect(self._on_trial_done)
        worker.error.connect(self._on_trial_error)
        worker.start()

    def _on_trial_transcribing(self):
        # Slot sur le thread GUI (queued connection).
        self._trial_status.setText("Transcription en cours…")

    def _on_trial_done(self, text: str):
        # Slot sur le thread GUI (queued connection).
        self._btn_trial.setEnabled(True)
        self._btn_trial.setText("Refaire une dictée d'essai")
        text = (text or "").strip()
        if text:
            self._trial_field.setText(text)
            self._trial_status.setText("✓ Voilà ce que Voxaho a compris.")
        else:
            self._trial_field.clear()
            self._trial_status.setText(
                "Aucun texte détecté — parlez un peu plus fort, puis réessayez."
            )

    def _on_trial_error(self, msg: str):
        # Slot sur le thread GUI (queued connection). L'essai est un bonus :
        # on n'affiche jamais d'erreur bloquante, juste une note rassurante.
        logger.debug(f"Dictée d'essai indisponible : {msg}")
        self._btn_trial.setEnabled(True)
        self._btn_trial.setText("Faire une dictée d'essai")
        self._trial_field.clear()
        self._trial_status.setText(
            "Essai indisponible, vous pourrez dicter après configuration."
        )

    # ── Final ────────────────────────────────────────────────────────────────

    def _finalize_config_text(self):
        if IS_MAC:
            key = "Fn"
        else:
            label_map = {
                "ctrl_r": "Ctrl Droit", "ctrl_l": "Ctrl Gauche",
                "alt_r":  "Alt Droit",  "shift_r": "Maj Droite",
                "caps_lock": "Verr Maj",
            }
            key = label_map.get(self._config_draft.get("win_key", "ctrl_r"), "Ctrl Droit")
        self._final_key.setText(f"Touche : {key}")

    # ── Cleanup ──────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._mic_timer:
            self._mic_timer.stop()
        try:
            if self._mic_stream is not None:
                self._mic_stream.stop()
                self._mic_stream.close()
        except Exception:
            pass
        if self._downloader and self._downloader.isRunning():
            # Ne PAS bloquer ici : quit() est sans effet (run() est bloquant,
            # pas de boucle d'événements dans le thread) et wait() gelait le
            # thread GUI jusqu'à 30 s. On déconnecte les signaux, on gare le
            # thread dans _orphan_threads (référence anti-GC, évite le crash
            # "QThread: Destroyed while thread is still running") et on ferme
            # immédiatement : le thread mourra avec le process.
            for sig in (self._downloader.progress,
                        self._downloader.finished,
                        self._downloader.error):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            _park_thread(self._downloader)
            self._downloader = None
        # Dictée d'essai en cours : même traitement anti-GC (déconnexion des
        # signaux + parking). Le worker n'a pas de parent et mourra avec le
        # process ; on évite ainsi le crash "QThread: Destroyed while running".
        if self._trial_worker is not None and self._trial_worker.isRunning():
            for sig in (self._trial_worker.transcribing,
                        self._trial_worker.finished_text,
                        self._trial_worker.error):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            _park_thread(self._trial_worker)
            self._trial_worker = None
        super().closeEvent(event)


# ── Dialog d'activation de licence (intact) ───────────────────────────────────

LICENSE_STYLESHEET = STYLESHEET + """
QPushButton#secondary {
    background-color: transparent; color: #8E8E93; border: none;
    padding: 8px 12px; font-size: 12px; text-decoration: underline;
}
QPushButton#secondary:hover { color: #FFFFFF; }
"""


class LicenseDialog(QDialog):
    """Écran d'activation de licence. Optionnellement permet le mode trial.

    Exit codes :
      - Accepted : licence activée OU trial accepté (`trial_chosen` True)
      - Rejected : utilisateur a fermé sans rien faire
    """

    def __init__(self, *, allow_trial: bool = True, parent=None):
        super().__init__(parent)
        self._allow_trial = allow_trial
        self.trial_chosen = False
        self._act_worker: _ActivationWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Voxaho — Activation")
        self.setFixedSize(460, 360)
        self.setStyleSheet(LICENSE_STYLESHEET)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.MSWindowsFixedSizeDialogHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(40, 36, 40, 28)

        title = QLabel("Activer Voxaho")
        title.setFont(QFont("-apple-system", 22, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(6)

        sub = QLabel("Entrez la clé reçue par email après votre achat.")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub)
        layout.addSpacing(28)

        lbl = QLabel("Clé de licence")
        lbl.setObjectName("section")
        layout.addWidget(lbl)
        layout.addSpacing(8)

        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX")
        layout.addWidget(self._key_input)
        layout.addSpacing(8)

        self._error = QLabel("")
        self._error.setObjectName("error")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)
        layout.addStretch()

        self._btn = QPushButton("Activer")
        self._btn.setObjectName("primary")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._on_activate)
        layout.addWidget(self._btn)
        layout.addSpacing(8)

        if self._allow_trial:
            self._trial_btn = QPushButton("Pas de clé ? Continuer en mode trial")
            self._trial_btn.setObjectName("secondary")
            self._trial_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._trial_btn.clicked.connect(self._on_trial)
            layout.addWidget(self._trial_btn)

    def _on_activate(self):
        key = self._key_input.text().strip()
        if not key:
            self._error.setText("Veuillez saisir une clé.")
            return
        if self._act_worker is not None and self._act_worker.isRunning():
            return  # activation déjà en cours
        self._btn.setEnabled(False)
        self._btn.setText("Activation…")
        self._error.setText("")
        # Activation dans un worker (appel réseau ~10 s) : l'UI reste
        # réactive. Le worker n'a pas de parent et est garé dans
        # _orphan_threads : si le dialog est fermé/détruit pendant l'appel,
        # Qt coupe les connexions et le thread meurt avec le process.
        self._act_worker = _ActivationWorker(key)
        _park_thread(self._act_worker)
        self._act_worker.success.connect(self._on_activation_success)
        self._act_worker.error.connect(self._on_activation_error)
        self._act_worker.start()

    def _on_activation_success(self):
        # Slot exécuté sur le thread GUI (queued connection).
        self.accept()

    def _on_activation_error(self, msg: str):
        # Slot exécuté sur le thread GUI (queued connection).
        self._error.setText(f"Échec : {msg}")
        self._btn.setEnabled(True)
        self._btn.setText("Activer")

    def _on_trial(self):
        self.trial_chosen = True
        self.accept()
