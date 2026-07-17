"""
SettingsWindow — fenêtre de personnalisation complète de Voxaho.

Panneau multi-section (sidebar gauche + contenu droite), ouvert depuis la barre
flottante. Émet :
  - settings_preview(dict) : sur changement immédiat (preview live)
  - settings_applied(dict) : sur clic "Appliquer" (sauvegardé + appliqué)
"""

from __future__ import annotations

import os
import sys
import logging
import webbrowser
from copy import deepcopy

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QLineEdit, QStackedWidget, QListWidget,
    QListWidgetItem, QButtonGroup, QRadioButton, QSlider, QFrame,
    QSpacerItem, QSizePolicy, QMessageBox, QProgressDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QEventLoop, QThread
from PyQt6.QtGui import QFont, QColor

# Version centralisée : core/__init__.py est la source unique de vérité.
from core import __version__

logger = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"


# ── Palette accent (id → (label, hex)) ──────────────────────────────────────────
ACCENTS = {
    "blue":   ("Bleu",   "#0A84FF"),
    "purple": ("Violet", "#BF5AF2"),
    "green":  ("Vert",   "#30D158"),
    "orange": ("Orange", "#FF9F0A"),
    "pink":   ("Rose",   "#FF375F"),
}

MINI_SIZES = {"small": 30, "medium": 40, "large": 52}

# Langues de dictée exposées dans l'UI (source unique, importée par le wizard).
# Chaque code est un code langue Whisper ISO-639-1 réellement supporté par le
# moteur (faster-whisper reconnaît ~100 langues nativement).
# Ordre : 1) langues prioritaires fr/en/es/de/it, 2) le reste par ordre
# alphabétique du nom natif (latin puis autres écritures), 3) « Auto » en fin.
# Chaque label = drapeau emoji + nom NATIF de la langue.
LANGS = [
    # ── Langues prioritaires ─────────────────────────────────────────────
    ("fr",   "🇫🇷  Français"),
    ("en",   "🇬🇧  English"),
    ("es",   "🇪🇸  Español"),
    ("de",   "🇩🇪  Deutsch"),
    ("it",   "🇮🇹  Italiano"),
    # ── Écriture latine (ordre alphabétique du nom natif) ────────────────
    ("id",   "🇮🇩  Bahasa Indonesia"),
    ("ms",   "🇲🇾  Bahasa Melayu"),
    ("ca",   "🇦🇩  Català"),
    ("cs",   "🇨🇿  Čeština"),
    ("da",   "🇩🇰  Dansk"),
    ("hr",   "🇭🇷  Hrvatski"),
    ("hu",   "🇭🇺  Magyar"),
    ("nl",   "🇳🇱  Nederlands"),
    ("no",   "🇳🇴  Norsk"),
    ("pl",   "🇵🇱  Polski"),
    ("pt",   "🇵🇹  Português"),
    ("ro",   "🇷🇴  Română"),
    ("sk",   "🇸🇰  Slovenčina"),
    ("sl",   "🇸🇮  Slovenščina"),
    ("fi",   "🇫🇮  Suomi"),
    ("sv",   "🇸🇪  Svenska"),
    ("vi",   "🇻🇳  Tiếng Việt"),
    ("tr",   "🇹🇷  Türkçe"),
    # ── Autres écritures (grec, cyrillique, hébreu, arabe, indiennes, CJK) ─
    ("el",   "🇬🇷  Ελληνικά"),
    ("bg",   "🇧🇬  Български"),
    ("ru",   "🇷🇺  Русский"),
    ("sr",   "🇷🇸  Српски"),
    ("uk",   "🇺🇦  Українська"),
    ("he",   "🇮🇱  עברית"),
    ("ar",   "🇸🇦  العربية"),
    ("fa",   "🇮🇷  فارسی"),
    ("hi",   "🇮🇳  हिन्दी"),
    ("ta",   "🇮🇳  தமிழ்"),
    ("th",   "🇹🇭  ไทย"),
    ("zh",   "🇨🇳  中文"),
    ("ja",   "🇯🇵  日本語"),
    ("ko",   "🇰🇷  한국어"),
    # ── Détection automatique ────────────────────────────────────────────
    ("auto", "🌍  Auto"),
]

# Liste des modèles Whisper (code, libellé combo, description perf).
# large-v3-turbo est le meilleur compromis mis en avant (« ✓ Recommandé ») ;
# small reste le défaut léger (rapide, empreinte disque minimale).
MODELS = [
    ("tiny",           "Tiny",                        "≈ 0,3 s · 200 Mo · qualité basique"),
    ("small",          "Small (léger)",               "≈ 0,8 s · 500 Mo · bon compromis léger"),
    ("medium",         "Medium",                      "≈ 1,5 s · 1,5 Go · meilleure qualité"),
    ("large-v3-turbo", "Large v3 Turbo (recommandé)", "≈ 1 s · 1,6 Go · qualité quasi-max, très rapide ✓ Recommandé"),
    ("large-v3",       "Large v3",                    "≈ 3 s · 3 Go · qualité maximale"),
]

# Presets vitesse/qualité mappés sur beam_size (faster-whisper).
# « Vitesse » = réactivité maximale ; « Qualité » = plus d'hypothèses explorées.
BEAM_PRESETS = {"speed": 1, "quality": 5}

WIN_KEYS = [
    ("ctrl_r",    "Ctrl droit"),
    ("ctrl_l",    "Ctrl gauche"),
    ("alt_r",     "Alt droit"),
    ("shift_r",   "Shift droit"),
    ("caps_lock", "Verr. Maj."),
]


def _model_is_cached(model_name: str) -> bool:
    """Vrai si le modèle faster-whisper est déjà dans le cache HuggingFace.

    Fonction pure (testable sans Qt) : vérifie l'existence du dossier
    ~/.cache/huggingface/hub/models--Systran--faster-whisper-<model>
    (mapping direct pour tiny/small/medium/large-v3).
    """
    cache_dir = os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface", "hub",
        f"models--Systran--faster-whisper-{model_name}",
    )
    return os.path.isdir(cache_dir)


def _import_llm():
    """Import différé et défensif du module de reformatage IA (core.llm).

    Le composant IA est fourni par un agent parallèle et peut être ABSENT de
    cette installation. On renvoie le module seulement s'il expose l'API du
    contrat (is_available / is_model_ready / download_model), sinon None —
    l'appelant traite alors le composant comme indisponible.
    """
    try:
        from core import llm
    except Exception:
        return None
    if not all(hasattr(llm, attr)
               for attr in ("is_available", "is_model_ready", "download_model")):
        return None
    return llm


def _translator_available() -> bool:
    """Vrai si le module de traduction à la volée est présent ET disponible.

    Import différé + défensif : core.translator est fourni par un agent
    parallèle et peut être ABSENT de cette installation. Toute erreur (module
    introuvable, is_available manquant, exception à l'appel) → False : la
    traduction est alors considérée comme indisponible et l'UI affiche la note
    invitant à activer d'abord le reformatage IA (qui télécharge le modèle).
    """
    try:
        from core import translator
        return bool(translator.is_available())
    except Exception:
        return False


def ai_status_label(available: bool, ready: bool) -> str:
    """Libellé d'état du composant de reformatage IA local (fonction pure).

    Testable sans Qt. Trois branches :
      - composant absent              → « Composant IA non installé »
      - présent mais modèle manquant  → « Modèle IA non téléchargé »
      - présent et prêt               → « ✓ Modèle IA prêt »
    """
    if not available:
        return "Composant IA non installé"
    if not ready:
        return "Modèle IA non téléchargé"
    return "✓ Modèle IA prêt"


# ── Worker de téléchargement du modèle IA (~1 Go) ────────────────────────────────
class _AiModelDownloader(QThread):
    """Télécharge le modèle IA local via core.llm.download_model, hors GUI.

    Même pattern que ModelDownloader / _ActivationWorker (setup_wizard) :
    signaux success/error remontés au thread GUI (queued connection), pas de
    parent (survit à la destruction du dialog, référence anti-GC gardée par
    SettingsWindow via _park_thread). L'import de core.llm est fait dans run()
    et reste défensif.
    """

    success = pyqtSignal()
    error   = pyqtSignal(str)

    def run(self):
        try:
            from core import llm
        except Exception as e:  # composant IA absent de cette installation
            self.error.emit(f"Composant IA indisponible : {e}")
            return
        try:
            ok = llm.download_model(progress_cb=None)
        except Exception as e:
            logger.error(f"_AiModelDownloader: {e}", exc_info=True)
            self.error.emit(str(e))
            return
        if ok:
            self.success.emit()
        else:
            self.error.emit("Le téléchargement du modèle IA a échoué.")


# ── Stylesheet global ───────────────────────────────────────────────────────────
STYLESHEET = """
QDialog { background-color: #1C1C1E; }
QWidget#sidebar { background-color: #0D0D18; }
QWidget#content { background-color: #1C1C1E; }
QLabel { color: #FFFFFF; background: transparent;
         font-family: -apple-system, "SF Pro Text", "Segoe UI", system-ui; }
QLabel#title    { color: #FFFFFF; font-size: 19px; font-weight: 600; }
QLabel#subtitle { color: #8E8E93; font-size: 12px; }
QLabel#section  { color: #EBEBF5; font-size: 11px; font-weight: 700;
                  letter-spacing: 1.2px; text-transform: uppercase; }
QLabel#hint     { color: #636366; font-size: 11px; }
QLabel#desc     { color: #8E8E93; font-size: 12px; }
QLabel#link     { color: #0A84FF; font-size: 12px; text-decoration: underline; }
QLabel#license-ok    { color: #30D158; font-size: 13px; font-weight: 600; }
QLabel#license-trial { color: #FF9F0A; font-size: 13px; font-weight: 600; }
QLabel#license-exp   { color: #FF453A; font-size: 13px; font-weight: 600; }

QListWidget#nav {
    background: transparent; border: none; outline: none;
    color: #EBEBF5; font-size: 13px;
    font-family: -apple-system, "SF Pro Text", system-ui;
    padding-top: 18px;
}
QListWidget#nav::item {
    padding: 11px 18px; margin: 2px 10px; border-radius: 8px;
    border-left: 2px solid transparent;
}
QListWidget#nav::item:hover    { background: rgba(255,255,255,0.06); }
QListWidget#nav::item:selected { background: rgba(10,132,255,0.15);
                                  border-left: 2px solid #0A84FF;
                                  color: #FFFFFF; }

QComboBox, QLineEdit {
    background-color: #2C2C2E; color: #FFFFFF;
    border: 1px solid #3A3A3C; border-radius: 8px;
    padding: 9px 12px; font-size: 13px;
    font-family: -apple-system, "SF Pro Text", system-ui;
}
QComboBox:focus, QLineEdit:focus { border: 1px solid #0A84FF; }
QComboBox::drop-down { border: none; padding-right: 12px; }
QComboBox QAbstractItemView {
    background-color: #2C2C2E; color: #FFFFFF;
    border: 1px solid #3A3A3C;
    selection-background-color: #0A84FF;
}
QComboBox:disabled, QLineEdit:disabled { color: #636366; background: #232325; }

QCheckBox { color: #FFFFFF; font-size: 13px; spacing: 10px; }
QCheckBox::indicator {
    width: 36px; height: 20px; border-radius: 10px;
    background: #3A3A3C; border: none;
}
QCheckBox::indicator:checked { background: #30D158; }

QRadioButton { color: #EBEBF5; font-size: 13px; spacing: 8px; padding: 3px 0; }
QRadioButton::indicator {
    width: 16px; height: 16px; border-radius: 8px;
    border: 1.5px solid #636366; background: #2C2C2E;
}
QRadioButton::indicator:checked {
    background: #0A84FF; border: 4px solid #0A84FF;
    width: 8px; height: 8px;
}

QPushButton {
    background-color: #2C2C2E; color: #EBEBF5;
    border: 1px solid #3A3A3C; border-radius: 8px;
    padding: 9px 16px; font-size: 13px;
    font-family: -apple-system, "SF Pro Text", system-ui;
}
QPushButton:hover    { background-color: #38383A; }
QPushButton:disabled { color: #636366; }

QPushButton#primary {
    background-color: #0A84FF; color: #FFFFFF; border: none;
    border-radius: 10px; padding: 11px 22px;
    font-size: 13px; font-weight: 600;
}
QPushButton#primary:hover    { background-color: #409CFF; }
QPushButton#primary:disabled { background-color: #3A3A3C; color: #636366; }

QPushButton#ghost {
    background-color: transparent; color: #EBEBF5;
    border: 1px solid #3A3A3C; border-radius: 10px;
    padding: 11px 22px; font-size: 13px; font-weight: 500;
}
QPushButton#ghost:hover { background-color: rgba(255,255,255,0.05); }

QPushButton#danger {
    background-color: transparent; color: #FF453A;
    border: 1px solid rgba(255,69,58,0.4); border-radius: 8px;
}
QPushButton#danger:hover { background-color: rgba(255,69,58,0.1); }

QSlider::groove:horizontal {
    height: 4px; background: #3A3A3C; border-radius: 2px;
}
QSlider::sub-page:horizontal { background: #0A84FF; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #FFFFFF; width: 16px; height: 16px;
    margin: -6px 0; border-radius: 8px;
}

QFrame#sep { background: #2C2C2E; max-height: 1px; border: none; }
"""


# ── Swatch couleur cliquable ────────────────────────────────────────────────────
class ColorSwatch(QPushButton):
    """Bouton circulaire coloré sélectionnable."""

    def __init__(self, accent_id: str, hex_color: str, parent=None):
        super().__init__(parent)
        self.accent_id = accent_id
        self.hex_color = hex_color
        self.setFixedSize(28, 28)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restyle()

    def _restyle(self):
        ring = "#FFFFFF" if self.isChecked() else "transparent"
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.hex_color};
                border: 2px solid {ring};
                border-radius: 14px;
            }}
            QPushButton:hover {{ border: 2px solid rgba(255,255,255,0.6); }}
        """)

    def setChecked(self, checked: bool):
        super().setChecked(checked)
        self._restyle()


# ── Fenêtre principale ──────────────────────────────────────────────────────────
class SettingsWindow(QDialog):

    settings_applied = pyqtSignal(dict)
    settings_preview = pyqtSignal(dict)

    def __init__(self, config: dict, save_config_fn):
        super().__init__()
        self._original_config = deepcopy(config)
        self.config = deepcopy(config)
        self._save_config = save_config_fn
        self._building = True  # gate les signaux pendant la construction
        self._act_worker = None   # worker d'activation licence (anti-GC)
        self._dl_worker = None    # worker de téléchargement modèle (anti-GC)
        self._dl_loop: QEventLoop | None = None
        self._dl_status: str | None = None
        self._dl_error = ""
        self._ai_dl_worker = None   # worker de téléchargement modèle IA (anti-GC)
        self._ai_dl_loop: QEventLoop | None = None
        self._ai_dl_status: str | None = None
        self._ai_dl_error = ""

        self._setup_ui()
        self._load_values()
        self._building = False

    # ── UI scaffold ──────────────────────────────────────────────────────────
    def _setup_ui(self):
        self.setWindowTitle("Voxaho — Préférences")
        self.setFixedSize(580, 640)
        self.setStyleSheet(STYLESHEET)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        sidebar = QWidget(objectName="sidebar")
        sidebar.setFixedWidth(160)
        sb_lay = QVBoxLayout(sidebar)
        sb_lay.setContentsMargins(0, 0, 0, 0)
        sb_lay.setSpacing(0)

        self.nav = QListWidget(objectName="nav")
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for label in ["⚙   Général", "🧠  Modèle", "🎙  Micro", "⌨   Touche",
                      "🎨  Apparence", "🔑  Licence", "ℹ   À propos"]:
            it = QListWidgetItem(label)
            it.setSizeHint(QSize(0, 40))
            self.nav.addItem(it)
        sb_lay.addWidget(self.nav)
        root.addWidget(sidebar)

        # Contenu droite
        right = QWidget(objectName="content")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(28, 24, 28, 20)
        right_lay.setSpacing(16)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_general_tab())
        self.stack.addWidget(self._build_model_tab())
        self.stack.addWidget(self._build_mic_tab())
        self.stack.addWidget(self._build_hotkey_tab())
        self.stack.addWidget(self._build_appearance_tab())
        self.stack.addWidget(self._build_license_tab())
        self.stack.addWidget(self._build_about_tab())
        right_lay.addWidget(self.stack, 1)

        # Boutons bas
        sep = QFrame(objectName="sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        right_lay.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Annuler", objectName="ghost")
        cancel.clicked.connect(self._on_cancel)
        apply_ = QPushButton("Appliquer", objectName="primary")
        apply_.clicked.connect(self._on_apply)
        btn_row.addWidget(cancel)
        btn_row.addSpacing(8)
        btn_row.addWidget(apply_)
        right_lay.addLayout(btn_row)

        root.addWidget(right, 1)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _tab_container(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        t = QLabel(title, objectName="title")
        lay.addWidget(t)
        if subtitle:
            s = QLabel(subtitle, objectName="subtitle")
            lay.addWidget(s)
        lay.addSpacing(6)
        return w, lay

    def _section_label(self, text: str) -> QLabel:
        return QLabel(text, objectName="section")

    # ── Onglet 1 : Général ───────────────────────────────────────────────────
    def _build_general_tab(self) -> QWidget:
        w, lay = self._tab_container("Général", "Langue, reformatage et démarrage")

        lay.addWidget(self._section_label("Langue de dictée"))
        self.cb_lang = QComboBox()
        for code, label in LANGS:
            self.cb_lang.addItem(label, code)
        self.cb_lang.currentIndexChanged.connect(self._emit_preview)
        lay.addWidget(self.cb_lang)

        lay.addSpacing(8)
        lay.addWidget(self._section_label("Reformatage IA"))
        self.ck_reformat = QCheckBox("  Activer le reformatage automatique")
        self.ck_reformat.stateChanged.connect(self._emit_preview)
        lay.addWidget(self.ck_reformat)
        hint = QLabel("Ajoute ponctuation, majuscules et corrige les erreurs courantes.",
                      objectName="desc")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # ── Reformatage IA (local) — vrai LLM local, option distincte ────────
        lay.addSpacing(12)
        lay.addWidget(self._section_label("Reformatage IA (local)"))
        self.ck_ai_reformat = QCheckBox("  Reformatage par IA locale (Qwen 2.5)")
        self.ck_ai_reformat.stateChanged.connect(self._on_ai_reformat_toggle)
        lay.addWidget(self.ck_ai_reformat)
        ai_hint = QLabel(
            "Reformule intelligemment votre dictée — 100 % local, hors-ligne. "
            "Nécessite un modèle de ~1 Go.",
            objectName="desc",
        )
        ai_hint.setWordWrap(True)
        lay.addWidget(ai_hint)

        # Zone d'état + action, mise à jour dynamiquement par _refresh_ai_status().
        ai_row = QHBoxLayout()
        ai_row.setSpacing(10)
        self.lb_ai_status = QLabel("", objectName="hint")
        self.lb_ai_status.setWordWrap(True)
        self.btn_ai_download = QPushButton("Télécharger le modèle (~1 Go)")
        self.btn_ai_download.clicked.connect(self._download_ai_model)
        ai_row.addWidget(self.lb_ai_status, 1)
        ai_row.addWidget(self.btn_ai_download)
        lay.addLayout(ai_row)
        # État initial (indispo / à télécharger / prêt) dès la construction.
        self._refresh_ai_status()

        # ── Traduction à la volée ────────────────────────────────────────────
        lay.addSpacing(12)
        lay.addWidget(self._section_label("Traduction"))
        tr_lbl = QLabel("Traduire la dictée vers…", objectName="desc")
        lay.addWidget(tr_lbl)
        self.cb_translate = QComboBox()
        # 1ʳᵉ entrée = désactivé (aucune traduction) → valeur None (currentData).
        self.cb_translate.addItem("Désactivé (garder la langue dictée)", None)
        # Puis toutes les langues de LANGS SAUF « auto » : on traduit vers une
        # langue PRÉCISE (une cible « automatique » n'aurait pas de sens).
        for code, label in LANGS:
            if code == "auto":
                continue
            self.cb_translate.addItem(label, code)
        self.cb_translate.currentIndexChanged.connect(self._emit_preview)
        lay.addWidget(self.cb_translate)

        tr_hint = QLabel(
            "Dictez dans n'importe quelle langue, Voxaho écrit dans celle "
            "choisie. Nécessite le modèle IA (Qwen).",
            objectName="desc",
        )
        tr_hint.setWordWrap(True)
        lay.addWidget(tr_hint)

        # Note défensive : si le composant de traduction est indisponible
        # (core.translator absent ou modèle IA non prêt), on invite à activer
        # d'abord le reformatage IA (qui déclenche le téléchargement du modèle).
        if not _translator_available():
            tr_note = QLabel(
                "Active d'abord le reformatage IA (télécharge le modèle).",
                objectName="hint",
            )
            tr_note.setWordWrap(True)
            lay.addWidget(tr_note)

        lay.addSpacing(12)
        lay.addWidget(self._section_label("Démarrage automatique"))
        self.ck_autostart = QCheckBox("  Lancer Voxaho à l'ouverture de session")
        from core import autostart
        if autostart.is_supported():
            self.ck_autostart.setChecked(autostart.is_enabled())
            self.ck_autostart.stateChanged.connect(self._on_autostart_toggle)
            hint_txt = "Voxaho démarrera silencieusement à chaque connexion."
        else:
            self.ck_autostart.setEnabled(False)
            hint_txt = "Non disponible sur cette plateforme."
        lay.addWidget(self.ck_autostart)
        self.lb_autostart_hint = QLabel(hint_txt, objectName="hint")
        self.lb_autostart_hint.setWordWrap(True)
        lay.addWidget(self.lb_autostart_hint)

        lay.addStretch(1)
        return w

    # ── Reformatage IA (local) : état + téléchargement ───────────────────────
    def _refresh_ai_status(self):
        """Rafraîchit l'état du composant IA sous la case « Reformatage IA ».

        Trois états (cf. ai_status_label) :
          - indisponible → label gris, case décochée + désactivée, bouton masqué ;
          - dispo mais modèle absent → label orangé + bouton « Télécharger » ;
          - dispo + prêt → label vert « ✓ Modèle IA prêt », bouton masqué.
        Défensif : core.llm peut être absent (agent parallèle). Appelée à la
        construction de l'onglet et après un téléchargement.
        """
        llm = _import_llm()
        available = bool(llm and llm.is_available())
        ready = bool(available and llm.is_model_ready())

        self.lb_ai_status.setText(ai_status_label(available, ready))

        if not available:
            # Composant IA non installé : on neutralise la case (décochée).
            self.lb_ai_status.setStyleSheet("color: #636366;")
            self.btn_ai_download.hide()
            self.ck_ai_reformat.blockSignals(True)
            self.ck_ai_reformat.setChecked(False)
            self.ck_ai_reformat.blockSignals(False)
            self.ck_ai_reformat.setEnabled(False)
        elif not ready:
            self.lb_ai_status.setStyleSheet("color: #FF9F0A;")
            self.btn_ai_download.show()
            self.btn_ai_download.setEnabled(True)
            self.ck_ai_reformat.setEnabled(True)
        else:
            self.lb_ai_status.setStyleSheet("color: #30D158; font-weight: 600;")
            self.btn_ai_download.hide()
            self.ck_ai_reformat.setEnabled(True)

    def _on_ai_reformat_toggle(self, *_):
        """Coche/décoche « Reformatage par IA locale ».

        Si l'utilisateur active alors que le modèle n'est pas prêt, on propose
        de le télécharger (non bloquant : le transcriber retombe sur les règles
        tant que le modèle est absent). Respecte le gate self._building.
        """
        if self._building:
            return
        if self.ck_ai_reformat.isChecked():
            llm = _import_llm()
            if llm and llm.is_available() and not llm.is_model_ready():
                resp = QMessageBox.question(
                    self, "Modèle IA requis",
                    "Le reformatage par IA nécessite un modèle (~1 Go) qui "
                    "n'est pas encore téléchargé.\n\nLe télécharger maintenant ?",
                )
                if resp == QMessageBox.StandardButton.Yes:
                    self._download_ai_model()
        self._emit_preview()

    def _download_ai_model(self):
        """Télécharge le modèle IA (~1 Go) dans un QThread, sans geler l'UI.

        QProgressDialog indéterminé + worker _AiModelDownloader (signaux
        success/error sur le thread GUI). On attend via une boucle
        d'événements locale — l'UI reste réactive. Référence anti-GC gardée
        (_park_thread) et signaux déconnectés en sortie. Succès → refresh +
        message ; échec → warning ; annulation → refresh silencieux.
        """
        llm = _import_llm()
        if not (llm and llm.is_available()):
            QMessageBox.warning(
                self, "Reformatage IA",
                "Le composant IA n'est pas disponible sur cette installation.",
            )
            return
        if self._ai_dl_worker is not None and self._ai_dl_worker.isRunning():
            return  # téléchargement déjà en cours

        # Import différé : évite un import circulaire au niveau module
        # (setup_wizard importe LANGS depuis ce module).
        from ui.setup_wizard import _park_thread

        dlg = QProgressDialog(
            "Téléchargement du modèle IA…\n"
            "~1 Go, cela peut prendre plusieurs minutes.",
            "Annuler", 0, 0, self,  # min == max == 0 → barre indéterminée
        )
        dlg.setWindowTitle("Voxaho — Téléchargement IA")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        self._ai_dl_status = None
        self._ai_dl_error = ""
        self._ai_dl_loop = QEventLoop(self)

        worker = _AiModelDownloader()
        self._ai_dl_worker = worker      # référence anti-GC
        _park_thread(worker)
        worker.success.connect(self._on_ai_dl_success)
        worker.error.connect(self._on_ai_dl_error)
        dlg.canceled.connect(self._on_ai_dl_canceled)
        worker.start()
        dlg.show()
        self._ai_dl_loop.exec()
        self._ai_dl_loop = None

        # Déconnexions : un signal tardif (worker abandonné après annulation)
        # ne doit pas rejouer les slots plus tard.
        for sig in (worker.success, worker.error):
            try:
                sig.disconnect()
            except TypeError:
                pass
        try:
            dlg.canceled.disconnect(self._on_ai_dl_canceled)
        except TypeError:
            pass
        dlg.close()
        dlg.deleteLater()

        if self._ai_dl_status == "ok":
            self._refresh_ai_status()
            QMessageBox.information(
                self, "Reformatage IA",
                "✓ Modèle IA téléchargé. Le reformatage par IA est prêt.",
            )
        elif self._ai_dl_status == "error":
            QMessageBox.warning(
                self, "Téléchargement échoué",
                f"Impossible de télécharger le modèle IA :\n{self._ai_dl_error}",
            )
            self._refresh_ai_status()
        else:
            # "cancel" : le run() est bloquant et non interruptible — le thread
            # reste garé dans _orphan_threads et mourra avec le process.
            self._refresh_ai_status()

    def _end_ai_download_wait(self, status: str, msg: str = ""):
        # Slot exécuté sur le thread GUI (queued connection).
        if self._ai_dl_status is None:
            self._ai_dl_status = status
            self._ai_dl_error = msg
        if self._ai_dl_loop is not None:
            self._ai_dl_loop.quit()

    def _on_ai_dl_success(self):
        self._end_ai_download_wait("ok")

    def _on_ai_dl_error(self, msg: str):
        self._end_ai_download_wait("error", msg)

    def _on_ai_dl_canceled(self):
        self._end_ai_download_wait("cancel")

    # ── Onglet 2 : Modèle ────────────────────────────────────────────────────
    def _build_model_tab(self) -> QWidget:
        w, lay = self._tab_container("Modèle Whisper", "Choisir la précision de la transcription")

        lay.addWidget(self._section_label("Modèle"))
        self.cb_model = QComboBox()
        for code, label, _ in MODELS:
            self.cb_model.addItem(label, code)
        self.cb_model.currentIndexChanged.connect(self._on_model_change)
        lay.addWidget(self.cb_model)

        self.lb_model_desc = QLabel("", objectName="desc")
        self.lb_model_desc.setWordWrap(True)
        lay.addWidget(self.lb_model_desc)

        # ── Vitesse vs Qualité (mappe sur beam_size) ──────────────────────
        lay.addSpacing(16)
        lay.addWidget(self._section_label("Vitesse / Qualité"))
        self.bg_beam = QButtonGroup(self)
        self.rb_speed = QRadioButton("  Vitesse — réactivité maximale")
        self.rb_speed.setProperty("beam_preset", "speed")
        self.rb_quality = QRadioButton("  Qualité — un peu plus lent, plus précis")
        self.rb_quality.setProperty("beam_preset", "quality")
        self.bg_beam.addButton(self.rb_speed)
        self.bg_beam.addButton(self.rb_quality)
        self.rb_speed.toggled.connect(self._emit_preview)
        lay.addWidget(self.rb_speed)
        lay.addWidget(self.rb_quality)
        beam_hint = QLabel(
            f"« Vitesse » explore une seule hypothèse (beam {BEAM_PRESETS['speed']}) ; "
            f"« Qualité » en explore plusieurs (beam {BEAM_PRESETS['quality']}).",
            objectName="hint",
        )
        beam_hint.setWordWrap(True)
        lay.addWidget(beam_hint)

        lay.addSpacing(16)
        redl = QPushButton("Re-télécharger le modèle")
        redl.clicked.connect(self._on_redownload)
        lay.addWidget(redl, alignment=Qt.AlignmentFlag.AlignLeft)

        lay.addStretch(1)
        return w

    def _on_model_change(self):
        idx = self.cb_model.currentIndex()
        if 0 <= idx < len(MODELS):
            self.lb_model_desc.setText(MODELS[idx][2])
        self._emit_preview()

    def _on_redownload(self):
        QMessageBox.information(
            self, "Re-télécharger",
            "Pour re-télécharger le modèle, supprimez le dossier ~/.cache/huggingface\n"
            "puis relancez Voxaho.",
        )

    # ── Onglet Micro ─────────────────────────────────────────────────────────
    def _build_mic_tab(self) -> QWidget:
        w, lay = self._tab_container("Micro", "Choisir le périphérique d'entrée")

        lay.addWidget(self._section_label("Microphone"))
        self.cb_mic = QComboBox()
        self.cb_mic.currentIndexChanged.connect(self._emit_preview)
        lay.addWidget(self.cb_mic)

        self.lb_mic_hint = QLabel("", objectName="hint")
        self.lb_mic_hint.setWordWrap(True)
        lay.addWidget(self.lb_mic_hint)

        lay.addSpacing(12)
        self.btn_mic_refresh = QPushButton("Rafraîchir la liste")
        self.btn_mic_refresh.clicked.connect(self._refresh_mic_list)
        lay.addWidget(self.btn_mic_refresh, alignment=Qt.AlignmentFlag.AlignLeft)

        lay.addStretch(1)
        # Peuplement initial (défensif : le helper moteur peut être indisponible).
        self._refresh_mic_list()
        return w

    def _refresh_mic_list(self):
        """(Re)construit la liste des micros via core.recorder.list_input_devices.

        Toujours une 1ʳᵉ entrée « Micro système par défaut » (valeur None), puis
        chaque périphérique (index stocké en currentData). Défensif : si le helper
        est indisponible (agent moteur pas encore prêt) ou renvoie une liste vide,
        on n'affiche que l'entrée par défaut et on désactive le combo.
        """
        # Préserve la sélection courante (index périphérique ou None).
        if self.cb_mic.count():
            current = self.cb_mic.currentData()
        else:
            current = self.config.get("input_device")

        self.cb_mic.blockSignals(True)
        self.cb_mic.clear()
        self.cb_mic.addItem("Micro système par défaut", None)

        devices = []
        try:
            from core.recorder import list_input_devices
            devices = list_input_devices() or []
        except Exception as e:  # ImportError, erreur PortAudio, etc.
            logger.debug(f"list_input_devices indisponible : {e}")
            devices = []

        if devices:
            self.cb_mic.setEnabled(True)
            for dev in devices:
                try:
                    idx = int(dev["index"])
                    name = str(dev.get("name", f"Périphérique {idx}"))
                    is_default = bool(dev.get("default", False))
                except (KeyError, TypeError, ValueError):
                    continue
                label = f"{name}  ✓ défaut système" if is_default else name
                self.cb_mic.addItem(label, idx)
            self.lb_mic_hint.setText(
                "« Micro système par défaut » suit le réglage du système. "
                "Sélectionnez un périphérique précis pour le forcer."
            )
        else:
            self.cb_mic.setEnabled(False)
            self.lb_mic_hint.setText(
                "Détection indisponible — le micro système par défaut sera utilisé."
            )

        # Restaure la sélection si elle existe encore, sinon défaut (index 0).
        if current is None:
            self.cb_mic.setCurrentIndex(0)
        else:
            pos = self.cb_mic.findData(current)
            self.cb_mic.setCurrentIndex(pos if pos >= 0 else 0)
        self.cb_mic.blockSignals(False)

    def _on_autostart_toggle(self, state):
        from core import autostart
        wanted = self.ck_autostart.isChecked()
        ok = autostart.set_enabled(wanted)
        if not ok:
            # Restaure la coche à l'état réel et préviens
            self.ck_autostart.blockSignals(True)
            self.ck_autostart.setChecked(autostart.is_enabled())
            self.ck_autostart.blockSignals(False)
            QMessageBox.warning(
                self, "Démarrage automatique",
                "Impossible de modifier le démarrage automatique.\n"
                "Vérifiez les permissions de ~/Library/LaunchAgents (macOS) "
                "ou du registre (Windows).",
            )
            return
        # Feedback visuel discret
        if wanted:
            self.lb_autostart_hint.setText("✓ Voxaho se lancera à votre prochaine session.")
        else:
            self.lb_autostart_hint.setText("Voxaho ne démarrera plus automatiquement.")

    # ── Onglet 3 : Touche ────────────────────────────────────────────────────
    def _build_hotkey_tab(self) -> QWidget:
        w, lay = self._tab_container("Touche déclencheur",
                                      "Maintenez cette touche pour dicter")

        lay.addWidget(self._section_label("Touche"))
        if IS_MAC:
            ro = QLineEdit("Fn (non modifiable sur macOS)")
            ro.setReadOnly(True)
            ro.setDisabled(True)
            lay.addWidget(ro)
            help_ = QLabel(
                "Sur macOS, Voxaho utilise la touche Fn (Globe) via une autorisation "
                "Accessibilité. Cette touche n'est pas reconfigurable.",
                objectName="desc",
            )
            help_.setWordWrap(True)
            lay.addWidget(help_)
        else:
            self.cb_winkey = QComboBox()
            for code, label in WIN_KEYS:
                self.cb_winkey.addItem(label, code)
            self.cb_winkey.currentIndexChanged.connect(self._emit_preview)
            lay.addWidget(self.cb_winkey)
            help_ = QLabel(
                "Maintenez cette touche pendant que vous parlez. Relâchez pour transcrire.",
                objectName="desc",
            )
            help_.setWordWrap(True)
            lay.addWidget(help_)

        lay.addStretch(1)
        return w

    # ── Onglet 4 : Apparence ─────────────────────────────────────────────────
    def _build_appearance_tab(self) -> QWidget:
        w, lay = self._tab_container("Apparence", "Position, couleur et taille")

        lay.addWidget(self._section_label("Position de la barre"))
        self.bg_pos = QButtonGroup(self)
        for code, label in [("top", "Haut centre"),
                             ("bottom", "Bas centre"),
                             ("custom", "Position personnalisée (glisser-déposer)")]:
            rb = QRadioButton(label)
            rb.setProperty("pos_code", code)
            rb.toggled.connect(self._emit_preview)
            self.bg_pos.addButton(rb)
            lay.addWidget(rb)

        lay.addSpacing(12)
        lay.addWidget(self._section_label("Couleur d'accent"))
        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(10)
        self.swatches: list[ColorSwatch] = []
        for acc_id, (lab, hex_c) in ACCENTS.items():
            s = ColorSwatch(acc_id, hex_c)
            s.setToolTip(lab)
            s.clicked.connect(lambda _=False, sw=s: self._on_swatch(sw))
            swatch_row.addWidget(s)
            self.swatches.append(s)
        swatch_row.addStretch(1)
        lay.addLayout(swatch_row)

        lay.addSpacing(12)
        lay.addWidget(self._section_label("Taille de la barre minimale"))
        size_row = QHBoxLayout()
        size_row.setSpacing(12)
        self.sl_size = QSlider(Qt.Orientation.Horizontal)
        self.sl_size.setRange(0, 2)
        self.sl_size.setTickInterval(1)
        self.sl_size.valueChanged.connect(self._on_size_change)
        self.lb_size = QLabel("Medium", objectName="desc")
        self.lb_size.setMinimumWidth(60)
        size_row.addWidget(self.sl_size, 1)
        size_row.addWidget(self.lb_size)
        lay.addLayout(size_row)

        lay.addSpacing(12)
        lay.addWidget(self._section_label("Visibilité"))
        self.ck_autohide = QCheckBox("  Masquer automatiquement après 5 s d'inactivité")
        self.ck_autohide.stateChanged.connect(self._emit_preview)
        lay.addWidget(self.ck_autohide)

        lay.addStretch(1)
        return w

    def _on_swatch(self, clicked: ColorSwatch):
        for s in self.swatches:
            s.setChecked(s is clicked)
        self._emit_preview()

    def _on_size_change(self, v: int):
        self.lb_size.setText(["Small", "Medium", "Large"][v])
        self._emit_preview()

    # ── Onglet 5 : Licence ───────────────────────────────────────────────────
    def _build_license_tab(self) -> QWidget:
        w, lay = self._tab_container("Licence", "Statut et activation")

        self.lb_lic_status = QLabel("", objectName="license-trial")
        lay.addWidget(self.lb_lic_status)

        self.lb_lic_detail = QLabel("", objectName="desc")
        self.lb_lic_detail.setWordWrap(True)
        lay.addWidget(self.lb_lic_detail)

        lay.addSpacing(12)

        # Bouton achat / désactivation
        self.btn_buy = QPushButton("Acheter Voxaho à vie · 19,99 €", objectName="primary")
        self.btn_buy.clicked.connect(lambda: webbrowser.open("https://voxaho.com"))
        lay.addWidget(self.btn_buy, alignment=Qt.AlignmentFlag.AlignLeft)

        self.btn_deactivate = QPushButton("Désactiver cette machine", objectName="danger")
        self.btn_deactivate.clicked.connect(self._on_deactivate)
        lay.addWidget(self.btn_deactivate, alignment=Qt.AlignmentFlag.AlignLeft)

        lay.addSpacing(16)
        lay.addWidget(self._section_label("Activer une licence"))
        key_row = QHBoxLayout()
        self.ed_key = QLineEdit()
        self.ed_key.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        self.btn_activate = QPushButton("Activer")
        self.btn_activate.clicked.connect(self._on_activate)
        key_row.addWidget(self.ed_key, 1)
        key_row.addWidget(self.btn_activate)
        lay.addLayout(key_row)

        lay.addStretch(1)
        self._refresh_license_status()
        return w

    def _refresh_license_status(self):
        try:
            from core import license as lic
            if lic.is_activated():
                stored = lic._load() or {}
                key = stored.get("key", "")
                masked = "····-····-····-" + key[-4:].upper() if len(key) >= 4 else "····"
                self.lb_lic_status.setText("Licence active")
                self.lb_lic_status.setObjectName("license-ok")
                self.lb_lic_detail.setText(f"Clé : {masked}")
                self.btn_buy.hide()
                self.btn_deactivate.show()
            else:
                ts = lic.trial_status()
                if ts.get("active"):
                    self.lb_lic_status.setText(f"Essai gratuit — {ts['days_left']} jour(s) restant(s)")
                    self.lb_lic_status.setObjectName("license-trial")
                    self.lb_lic_detail.setText("Achetez une licence pour continuer après l'expiration.")
                else:
                    self.lb_lic_status.setText("Essai expiré")
                    self.lb_lic_status.setObjectName("license-exp")
                    self.lb_lic_detail.setText("Entrez une clé pour activer Voxaho.")
                self.btn_buy.show()
                self.btn_deactivate.hide()
            # Réappliquer styles après changement d'objectName
            self.lb_lic_status.setStyleSheet("")
            self.lb_lic_status.style().unpolish(self.lb_lic_status)
            self.lb_lic_status.style().polish(self.lb_lic_status)
        except Exception as e:
            logger.warning(f"_refresh_license_status: {e}")
            self.lb_lic_status.setText("Statut indisponible")

    def _on_activate(self):
        key = self.ed_key.text().strip()
        if not key:
            return
        if self._act_worker is not None and self._act_worker.isRunning():
            return  # activation déjà en cours
        # Import différé : évite un import circulaire au niveau module
        # (setup_wizard importe LANGS depuis ce module).
        from ui.setup_wizard import _ActivationWorker, _park_thread
        self.btn_activate.setEnabled(False)
        self.btn_activate.setText("Activation…")
        # Activation dans un worker (appel réseau ~10 s) : l'UI reste
        # réactive. Le worker n'a pas de parent et est garé dans
        # _orphan_threads : si la fenêtre est fermée pendant l'appel,
        # Qt coupe les connexions et le thread meurt avec le process.
        self._act_worker = _ActivationWorker(key)
        _park_thread(self._act_worker)
        self._act_worker.success.connect(self._on_activation_success)
        self._act_worker.error.connect(self._on_activation_error)
        self._act_worker.start()

    def _reset_activate_btn(self):
        self.btn_activate.setEnabled(True)
        self.btn_activate.setText("Activer")

    def _on_activation_success(self):
        # Slot exécuté sur le thread GUI (queued connection).
        self._reset_activate_btn()
        QMessageBox.information(self, "Licence", "Activation réussie !")
        self.ed_key.clear()
        self._refresh_license_status()

    def _on_activation_error(self, msg: str):
        # Slot exécuté sur le thread GUI (queued connection).
        self._reset_activate_btn()
        QMessageBox.warning(self, "Activation échouée", msg)

    def _on_deactivate(self):
        ok = QMessageBox.question(
            self, "Désactiver",
            "Désactiver cette machine ? Vous pourrez la réactiver plus tard avec la même clé.",
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            from core import license as lic
            lic.deactivate()
            self._refresh_license_status()
        except Exception as e:
            QMessageBox.warning(self, "Erreur", str(e))

    # ── Onglet 6 : À propos ──────────────────────────────────────────────────
    def _build_about_tab(self) -> QWidget:
        import os
        w, lay = self._tab_container("À propos", "")

        # Logo SVG centré en haut
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "voxaho-icon.svg",
        )
        if os.path.exists(icon_path):
            try:
                from PyQt6.QtSvgWidgets import QSvgWidget
                logo = QSvgWidget(icon_path)
                logo.setFixedSize(96, 96)
                lay.addWidget(logo, alignment=Qt.AlignmentFlag.AlignHCenter)
                lay.addSpacing(16)
            except ImportError:
                # QtSvgWidgets indisponible — on saute silencieusement
                pass

        title = QLabel("Voxaho", objectName="title")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title)

        ver = QLabel(f"Version {__version__}", objectName="desc")
        ver.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(ver)

        lay.addSpacing(8)
        credit = QLabel("Propulsé par Whisper · Conçu par Serge AHOUANSINOU", objectName="desc")
        credit.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(credit)

        lay.addSpacing(24)
        links_row = QWidget()
        links_lay = QHBoxLayout(links_row)
        links_lay.setContentsMargins(0, 0, 0, 0)
        links_lay.setSpacing(20)
        links_lay.addStretch(1)
        for label, url in [
            ("voxaho.com",       "https://voxaho.com"),
            ("Support",          "https://voxaho.com/support"),
            ("Confidentialité",  "https://voxaho.com/privacy"),
        ]:
            link = QLabel(f'<a style="color:#0A84FF; text-decoration:none;" href="{url}">{label}</a>')
            link.setTextFormat(Qt.TextFormat.RichText)
            link.setOpenExternalLinks(True)
            links_lay.addWidget(link)
        links_lay.addStretch(1)
        lay.addWidget(links_row)

        lay.addSpacing(24)
        btn_upd = QPushButton("Vérifier les mises à jour")
        btn_upd.clicked.connect(lambda: QMessageBox.information(
            self, "Mises à jour", "Vous êtes à jour."))
        lay.addWidget(btn_upd, alignment=Qt.AlignmentFlag.AlignHCenter)

        lay.addStretch(1)
        return w

    # ── Chargement valeurs initiales ─────────────────────────────────────────
    def _load_values(self):
        cfg = self.config

        # Langue
        lang = cfg.get("language", "fr")
        idx = next((i for i, (c, _) in enumerate(LANGS) if c == lang), 0)
        self.cb_lang.setCurrentIndex(idx)

        # Reformatage (par règles)
        self.ck_reformat.setChecked(bool(cfg.get("reformatting", True)))

        # Reformatage IA (local) — défaut False si absent. On réaligne ensuite
        # l'état visuel : si le composant est indisponible, _refresh_ai_status
        # re-décoche et désactive la case (cohérence avec la dispo réelle).
        self.ck_ai_reformat.setChecked(bool(cfg.get("ai_reformat", False)))
        self._refresh_ai_status()

        # Traduction : code langue cible (None = désactivé, défaut). Index 0 =
        # entrée « Désactivé » (currentData None) ; sinon on retrouve le code.
        translate_to = cfg.get("translate_to", None)
        if translate_to is None:
            self.cb_translate.setCurrentIndex(0)
        else:
            pos = self.cb_translate.findData(translate_to)
            self.cb_translate.setCurrentIndex(pos if pos >= 0 else 0)

        # Modèle
        model = cfg.get("model", "small")
        idx = next((i for i, (c, _, _) in enumerate(MODELS) if c == model), 1)
        self.cb_model.setCurrentIndex(idx)
        self.lb_model_desc.setText(MODELS[idx][2])

        # Vitesse / Qualité (beam_size) : > vitesse ⇒ Qualité, sinon Vitesse.
        try:
            beam = int(cfg.get("beam_size", BEAM_PRESETS["speed"]))
        except (TypeError, ValueError):
            beam = BEAM_PRESETS["speed"]
        if beam <= BEAM_PRESETS["speed"]:
            self.rb_speed.setChecked(True)
        else:
            self.rb_quality.setChecked(True)

        # Micro : sélectionne l'index périphérique enregistré (None = défaut).
        dev = cfg.get("input_device", None)
        if dev is None:
            self.cb_mic.setCurrentIndex(0)
        else:
            pos = self.cb_mic.findData(dev)
            self.cb_mic.setCurrentIndex(pos if pos >= 0 else 0)

        # Touche
        if not IS_MAC:
            wk = cfg.get("win_key", "ctrl_r")
            idx = next((i for i, (c, _) in enumerate(WIN_KEYS) if c == wk), 0)
            self.cb_winkey.setCurrentIndex(idx)

        # Position
        pos = cfg.get("bar_position", "custom")
        for btn in self.bg_pos.buttons():
            if btn.property("pos_code") == pos:
                btn.setChecked(True)
                break
        else:
            self.bg_pos.buttons()[2].setChecked(True)

        # Accent
        accent = cfg.get("accent", "blue")
        for s in self.swatches:
            s.setChecked(s.accent_id == accent)

        # Taille mini
        size = cfg.get("mini_size", "medium")
        size_idx = {"small": 0, "medium": 1, "large": 2}.get(size, 1)
        self.sl_size.setValue(size_idx)
        self.lb_size.setText(["Small", "Medium", "Large"][size_idx])

        # Auto-hide
        self.ck_autohide.setChecked(bool(cfg.get("auto_hide", True)))

    # ── Récupération valeurs ─────────────────────────────────────────────────
    def _collect(self) -> dict:
        cfg = deepcopy(self.config)
        cfg["language"]     = self.cb_lang.currentData()
        cfg["reformatting"] = self.ck_reformat.isChecked()
        cfg["ai_reformat"]  = self.ck_ai_reformat.isChecked()
        # Traduction : None (désactivé) ou code langue cible (currentData).
        cfg["translate_to"] = self.cb_translate.currentData()
        cfg["model"]        = self.cb_model.currentData()
        if not IS_MAC:
            cfg["win_key"]  = self.cb_winkey.currentData()

        # Vitesse / Qualité → beam_size
        cfg["beam_size"] = (BEAM_PRESETS["quality"] if self.rb_quality.isChecked()
                            else BEAM_PRESETS["speed"])

        # Micro : None (défaut système) ou index périphérique
        cfg["input_device"] = self.cb_mic.currentData()

        # Position
        for btn in self.bg_pos.buttons():
            if btn.isChecked():
                cfg["bar_position"] = btn.property("pos_code")
                break

        # Accent
        for s in self.swatches:
            if s.isChecked():
                cfg["accent"] = s.accent_id
                break

        cfg["mini_size"] = ["small", "medium", "large"][self.sl_size.value()]
        cfg["auto_hide"] = self.ck_autohide.isChecked()
        return cfg

    # ── Signaux ──────────────────────────────────────────────────────────────
    def _emit_preview(self, *_):
        if self._building:
            return
        try:
            self.settings_preview.emit(self._collect())
        except Exception as e:
            logger.warning(f"settings_preview: {e}")

    def _on_apply(self):
        cfg = self._collect()

        # Nouveau modèle absent du cache local : téléchargement explicite
        # AVANT d'appliquer — sinon il se téléchargeait silencieusement
        # (jusqu'à 3 Go) à la première dictée et l'app semblait plantée.
        old_model = self._original_config.get("model", "small")
        new_model = cfg.get("model", old_model)
        if new_model != old_model and not _model_is_cached(new_model):
            if not self._download_model_with_progress(new_model):
                # Annulation ou échec : on n'applique pas le changement de
                # modèle (revert du combo) — le reste s'applique quand même.
                cfg["model"] = old_model
                idx = next((i for i, (c, _, _) in enumerate(MODELS)
                            if c == old_model), 1)
                self.cb_model.setCurrentIndex(idx)

        try:
            self._save_config(cfg)
        except Exception as e:
            QMessageBox.warning(self, "Sauvegarde", f"Impossible de sauver : {e}")
            return
        self.config = cfg
        self._original_config = deepcopy(cfg)
        self.settings_applied.emit(cfg)
        self.accept()

    # ── Téléchargement de modèle (depuis les Préférences) ────────────────────
    def _download_model_with_progress(self, model: str) -> bool:
        """Télécharge le modèle dans un QThread avec QProgressDialog.

        Retourne True si le modèle est prêt, False si annulation ou erreur
        (un QMessageBox.warning est alors affiché pour l'erreur). L'UI reste
        réactive : on attend via une boucle d'événements locale, et les
        signaux du worker arrivent sur le thread GUI (queued connection).
        """
        # Import différé : évite un import circulaire au niveau module
        # (setup_wizard importe LANGS depuis ce module).
        from ui.setup_wizard import ModelDownloader, _park_thread

        dlg = QProgressDialog(
            f"Téléchargement du modèle « {model} »…\n"
            "Cela peut prendre plusieurs minutes.",
            "Annuler", 0, 0, self,  # min == max == 0 → barre indéterminée
        )
        dlg.setWindowTitle("Voxaho — Téléchargement")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        self._dl_status = None
        self._dl_error = ""
        self._dl_loop = QEventLoop(self)

        worker = ModelDownloader(model)
        self._dl_worker = worker      # référence anti-GC
        _park_thread(worker)
        worker.finished.connect(self._on_dl_finished)
        worker.error.connect(self._on_dl_error)
        dlg.canceled.connect(self._on_dl_canceled)
        worker.start()
        dlg.show()
        self._dl_loop.exec()
        self._dl_loop = None

        # Déconnexions : évite qu'un signal tardif (worker abandonné après
        # annulation, close du dialog) ne rejoue les slots plus tard.
        for sig in (worker.finished, worker.error):
            try:
                sig.disconnect()
            except TypeError:
                pass
        try:
            dlg.canceled.disconnect(self._on_dl_canceled)
        except TypeError:
            pass
        dlg.close()
        dlg.deleteLater()

        if self._dl_status == "ok":
            return True
        if self._dl_status == "error":
            QMessageBox.warning(
                self, "Téléchargement échoué",
                f"Impossible de télécharger le modèle « {model} » :\n"
                f"{self._dl_error}\n\nLe modèle actuel est conservé.",
            )
        # "cancel" : le thread ne peut pas être interrompu proprement
        # (run() bloquant) — il reste garé dans _orphan_threads et mourra
        # avec le process ; le modèle ne sera simplement pas appliqué.
        return False

    def _end_download_wait(self, status: str, msg: str = ""):
        # Slot exécuté sur le thread GUI (queued connection).
        if self._dl_status is None:
            self._dl_status = status
            self._dl_error = msg
        if self._dl_loop is not None:
            self._dl_loop.quit()

    def _on_dl_finished(self):
        self._end_download_wait("ok")

    def _on_dl_error(self, msg: str):
        self._end_download_wait("error", msg)

    def _on_dl_canceled(self):
        self._end_download_wait("cancel")

    def _on_cancel(self):
        # Restaurer config initiale en preview
        try:
            self.settings_preview.emit(self._original_config)
        except Exception:
            pass
        self.reject()

    def closeEvent(self, event):
        # Bouton fenêtre = annuler
        try:
            self.settings_preview.emit(self._original_config)
        except Exception:
            pass
        super().closeEvent(event)
