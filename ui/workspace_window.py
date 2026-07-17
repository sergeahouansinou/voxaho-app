"""
WorkspaceWindow — la fenêtre principale (« maison ») de Voxaho.

Fenêtre à sidebar sombre (gauche) + zone de contenu (droite), dans l'esprit
Notion : header workspace, navigation principale surlignée coin arrondi,
séparateur, et bas de sidebar avec « Réglages ». La zone de droite est un
QStackedWidget dont la page change selon la sélection de la sidebar.

Sections : Accueil (dashboard), Historique, Notes, Dictionnaire, Snippets,
Statistiques, Réunion (transcription continue horodatée — PHASE 3).

La couche données (core.history / core.notes / core.stats / core.dictionary /
core.snippets) est fournie par un agent parallèle : TOUS les accès sont
défensifs (try/except au niveau des méthodes). Si un module est indisponible,
on affiche « Aucune donnée » (ou « Aucun terme » / « Aucun snippet ») au lieu
de crasher.

Palette réutilisée telle quelle depuis ui/settings_window.py.
"""

from __future__ import annotations

import os
import sys
import logging
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QStackedWidget, QLineEdit,
    QTextEdit, QScrollArea, QFrame, QCheckBox, QMessageBox, QApplication,
    QSizePolicy, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QEvent, QRectF
from PyQt6.QtGui import QPainter, QColor

logger = logging.getLogger(__name__)


# ── Fonctions PURES (testables sans Qt) ──────────────────────────────────────

# Abréviations de mois FR (locale simple, aucune dépendance externe).
_FR_MONTHS = [
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
]


def format_iso_datetime(iso: str) -> str:
    """Formate une date ISO 8601 en « 17 juil. 14:32 » (locale FR simple).

    Robuste : gère le suffixe « Z », les fractions de seconde et les dates
    seules (sans heure). Retourne la chaîne d'origine si non parsable, et
    une chaîne vide pour une entrée vide/None.
    """
    if not iso:
        return ""
    s = str(iso).strip()
    # datetime.fromisoformat n'accepte le « Z » qu'à partir de 3.11 ; on le
    # normalise à la main pour rester tolérant.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return str(iso)
    month = _FR_MONTHS[dt.month - 1]
    return f"{dt.day} {month} {dt.hour:02d}:{dt.minute:02d}"


def truncate_text(s: str, n: int = 80) -> str:
    """Tronque une chaîne à n caractères (une seule ligne, ellipse « … »).

    Normalise d'abord les espaces et retours à la ligne pour un affichage
    propre en liste. Fonction pure, sans dépendance Qt.
    """
    if s is None:
        return ""
    s = " ".join(str(s).split())  # aplatit les retours ligne / espaces multiples
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def snippet_preview(trigger, expansion, n: int = 60) -> str:
    """Représentation compacte « déclencheur → texte tronqué » (pour la liste).

    Fonction pure, sans dépendance Qt. Normalise les espaces du déclencheur et
    tronque l'expansion via truncate_text. Retourne une chaîne vide si les deux
    champs sont vides.
    """
    trig = " ".join(str(trigger or "").split())
    exp = truncate_text(expansion, n)
    if not trig and not exp:
        return ""
    return f"{trig} → {exp}"


def format_minutes(minutes) -> str:
    """Formate une durée en minutes vers « 3 h 20 » ou « 45 min » (FR).

    Fonction pure : robuste aux valeurs None / non numériques (→ « 0 min »).
    """
    try:
        m = int(round(float(minutes)))
    except (TypeError, ValueError):
        return "0 min"
    if m < 0:
        m = 0
    if m < 60:
        return f"{m} min"
    h, rem = divmod(m, 60)
    if rem == 0:
        return f"{h} h"
    return f"{h} h {rem:02d}"


# ── Accès défensif à la couche données ───────────────────────────────────────
# Les imports sont faits au moment de l'appel (pas au niveau module) pour que
# l'import de ui.workspace_window ne dépende jamais de core.history/notes/stats.

def _mod(name: str):
    """Importe core.<name> à la volée ; retourne None si indisponible."""
    try:
        import importlib
        return importlib.import_module(f"core.{name}")
    except Exception as e:  # ImportError et toute erreur d'init du module
        logger.debug("core.%s indisponible : %s", name, e)
        return None


# ── Stylesheet global (palette identique à settings_window.py) ────────────────
STYLESHEET = """
QMainWindow { background-color: #1C1C1E; }
QWidget#sidebar { background-color: #0D0D18; }
QWidget#content { background-color: #1C1C1E; }
QWidget#page    { background-color: #1C1C1E; }

QLabel { color: #FFFFFF; background: transparent;
         font-family: -apple-system, "SF Pro Text", "Segoe UI", system-ui; }
QLabel#brand    { color: #FFFFFF; font-size: 16px; font-weight: 700; }
QLabel#title    { color: #FFFFFF; font-size: 22px; font-weight: 600; }
QLabel#subtitle { color: #8E8E93; font-size: 13px; }
QLabel#section  { color: #EBEBF5; font-size: 11px; font-weight: 700;
                  letter-spacing: 1.2px; text-transform: uppercase; }
QLabel#desc     { color: #8E8E93; font-size: 12px; }
QLabel#hint     { color: #636366; font-size: 11px; }
QLabel#empty    { color: #8E8E93; font-size: 13px; }

QLabel#card-value { color: #FFFFFF; font-size: 26px; font-weight: 700; }
QLabel#card-label { color: #8E8E93; font-size: 12px; }
QLabel#big-value  { color: #0A84FF; font-size: 34px; font-weight: 800; }

QLabel#entry-text { color: #FFFFFF; font-size: 13px; }
QLabel#entry-meta { color: #8E8E93; font-size: 11px; }

QListWidget#nav {
    background: transparent; border: none; outline: none;
    color: #EBEBF5; font-size: 13px;
    font-family: -apple-system, "SF Pro Text", system-ui;
    padding-top: 6px;
}
QListWidget#nav::item {
    padding: 11px 16px; margin: 2px 10px; border-radius: 8px;
    border-left: 2px solid transparent;
}
QListWidget#nav::item:hover    { background: rgba(255,255,255,0.06); }
QListWidget#nav::item:selected { background: rgba(10,132,255,0.15);
                                 border-left: 2px solid #0A84FF;
                                 color: #FFFFFF; }

QListWidget#notes-list {
    background: #161622; border: 1px solid #2C2C2E; border-radius: 10px;
    outline: none; color: #EBEBF5; font-size: 13px; padding: 4px;
}
QListWidget#notes-list::item { padding: 9px 10px; border-radius: 6px; }
QListWidget#notes-list::item:hover    { background: rgba(255,255,255,0.05); }
QListWidget#notes-list::item:selected { background: rgba(10,132,255,0.18);
                                        color: #FFFFFF; }

QLineEdit, QTextEdit {
    background-color: #2C2C2E; color: #FFFFFF;
    border: 1px solid #3A3A3C; border-radius: 8px;
    padding: 9px 12px; font-size: 13px;
    font-family: -apple-system, "SF Pro Text", system-ui;
}
QLineEdit:focus, QTextEdit:focus { border: 1px solid #0A84FF; }
QLineEdit#search { padding-left: 12px; }

QCheckBox { color: #FFFFFF; font-size: 13px; spacing: 10px; }
QCheckBox::indicator {
    width: 36px; height: 20px; border-radius: 10px;
    background: #3A3A3C; border: none;
}
QCheckBox::indicator:checked { background: #30D158; }

QPushButton {
    background-color: #2C2C2E; color: #EBEBF5;
    border: 1px solid #3A3A3C; border-radius: 8px;
    padding: 9px 16px; font-size: 13px;
    font-family: -apple-system, "SF Pro Text", system-ui;
}
QPushButton:hover { background-color: #38383A; }

QPushButton#primary {
    background-color: #0A84FF; color: #FFFFFF; border: none;
    border-radius: 10px; padding: 10px 18px; font-size: 13px; font-weight: 600;
}
QPushButton#primary:hover { background-color: #409CFF; }

QPushButton#ghost {
    background-color: transparent; color: #EBEBF5;
    border: 1px solid #3A3A3C; border-radius: 10px;
    padding: 10px 18px; font-size: 13px; font-weight: 500;
}
QPushButton#ghost:hover { background-color: rgba(255,255,255,0.05); }

QPushButton#danger {
    background-color: transparent; color: #FF453A;
    border: 1px solid rgba(255,69,58,0.4); border-radius: 8px;
}
QPushButton#danger:hover { background-color: rgba(255,69,58,0.1); }

QPushButton#icon {
    background-color: transparent; border: none; border-radius: 6px;
    padding: 4px 6px; font-size: 15px;
}
QPushButton#icon:hover { background-color: rgba(255,255,255,0.08); }

QPushButton#settings {
    background-color: transparent; color: #EBEBF5;
    border: none; border-radius: 8px; text-align: left;
    padding: 11px 16px; margin: 2px 10px; font-size: 13px;
}
QPushButton#settings:hover { background-color: rgba(255,255,255,0.06); }

QFrame#card {
    background-color: #2C2C2E; border: 1px solid #3A3A3C; border-radius: 12px;
}
QFrame#entry {
    background-color: #2C2C2E; border: 1px solid #3A3A3C; border-radius: 10px;
}
QFrame#sep { background: #2C2C2E; max-height: 1px; border: none; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #3A3A3C; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


# ── Mini graphe en barres (mots/jour), dessiné à la main (QPainter) ───────────
class _MiniBarChart(QWidget):
    """Petit histogramme des mots par jour, sans dépendance externe.

    Même approche que VUMeter (setup_wizard.py) : paintEvent + QPainter.
    Robuste au cas vide : affiche « Aucune donnée » centré.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list = []  # liste de (label, valeur)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_data(self, data):
        """data : list[dict {date, words}] ou list[(label, valeur)]."""
        norm = []
        for item in (data or []):
            try:
                if isinstance(item, dict):
                    norm.append((str(item.get("date", "")), int(item.get("words", 0))))
                else:
                    lbl, val = item
                    norm.append((str(lbl), int(val)))
            except (TypeError, ValueError):
                continue
        self._data = norm
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad = 8

        values = [v for _, v in self._data]
        if not values or max(values) <= 0:
            p.setPen(QColor("#8E8E93"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Aucune donnée")
            p.end()
            return

        vmax = max(values)
        n = len(values)
        gap = 3 if n <= 40 else 1
        chart_h = h - 2 * pad
        bar_w = (w - 2 * pad - (n - 1) * gap) / n
        if bar_w < 1:
            bar_w = 1.0

        for i, v in enumerate(values):
            x = pad + i * (bar_w + gap)
            bh = (max(0, v) / vmax) * chart_h
            y = pad + (chart_h - bh)
            p.fillRect(QRectF(x, y, bar_w, bh), QColor("#0A84FF"))
        p.end()


# ── Fenêtre principale ────────────────────────────────────────────────────────
class WorkspaceWindow(QMainWindow):
    """Fenêtre principale de Voxaho (sidebar + contenu à onglets)."""

    # Émis par l'intégration lorsqu'une nouvelle dictée est enregistrée ;
    # connecté à refresh() pour recharger la page courante en direct.
    dictation_added = pyqtSignal()

    # Émis (depuis le callback on_segment de MeetingSession, potentiellement sur
    # un thread worker) à chaque nouveau segment de réunion. Passer par un signal
    # Qt garantit une remontée THREAD-SAFE vers le slot qui append au transcript.
    meeting_segment = pyqtSignal(dict)

    # Index des pages dans le QStackedWidget (aligné sur l'ordre de la nav).
    # Dictionnaire et Snippets sont insérés après Notes et avant Statistiques :
    # cela décale l'index de Statistiques (3 → 5). La page Réunion est ajoutée EN
    # DERNIER (index 6), ce qui laisse les index existants inchangés.
    PAGE_HOME = 0
    PAGE_HISTORY = 1
    PAGE_NOTES = 2
    PAGE_DICTIONARY = 3
    PAGE_SNIPPETS = 4
    PAGE_STATS = 5
    PAGE_MEETING = 6

    def __init__(self, config: dict, save_config_fn, open_settings_fn=None):
        super().__init__()
        self.config = config or {}
        self._save_config = save_config_fn
        self._open_settings = open_settings_fn

        # État Notes : id de la note en cours d'édition (anti-GC : les widgets
        # d'édition sont gardés comme attributs d'instance, Qt les possède via
        # leur parent, et on conserve la référence pour les manipuler/sauver).
        self._current_note_id = None
        self._loading_note = False  # gate l'auto-save pendant le chargement

        # État Snippets : id du snippet en cours d'édition (None = mode ajout).
        self._editing_snippet_id = None

        # État Réunion : session en cours (core.meeting.MeetingSession) + segments
        # accumulés (pour l'export / l'enregistrement en note). Anti-GC : la
        # session est gardée comme attribut d'instance tant qu'elle tourne.
        self._meeting_session = None
        self._meeting_segments: list[dict] = []

        self._setup_ui()
        self.dictation_added.connect(self.refresh)
        # Remontée thread-safe des segments live vers le slot d'affichage.
        self.meeting_segment.connect(self._on_meeting_segment)

    # ── Construction de l'UI ─────────────────────────────────────────────────
    def _setup_ui(self):
        self.setWindowTitle("Voxaho")
        self.resize(900, 620)
        self.setMinimumSize(760, 520)
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        # Zone de contenu (QStackedWidget une page par section).
        content = QWidget(objectName="content")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        # L'ordre des addWidget DOIT rester synchronisé avec l'ordre des items
        # de la nav (cf. _build_sidebar) : la sélection de la sidebar pilote
        # directement stack.setCurrentIndex(row).
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_dashboard_page())    # 0
        self.stack.addWidget(self._build_history_page())      # 1
        self.stack.addWidget(self._build_notes_page())        # 2
        self.stack.addWidget(self._build_dictionary_page())   # 3
        self.stack.addWidget(self._build_snippets_page())     # 4
        self.stack.addWidget(self._build_stats_page())        # 5
        self.stack.addWidget(self._build_meeting_page())      # 6
        content_lay.addWidget(self.stack, 1)
        root.addWidget(content, 1)

        self.setCentralWidget(central)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(self.PAGE_HOME)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget(objectName="sidebar")
        sidebar.setFixedWidth(200)
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header workspace : logo + « Voxaho ».
        header = QWidget()
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(18, 20, 18, 14)
        h_lay.setSpacing(10)
        logo = self._make_logo()
        if logo is not None:
            h_lay.addWidget(logo)
        h_lay.addWidget(QLabel("Voxaho", objectName="brand"))
        h_lay.addStretch(1)
        lay.addWidget(header)

        # Navigation principale.
        self.nav = QListWidget(objectName="nav")
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Ordre synchronisé avec l'ajout des pages au QStackedWidget (_setup_ui).
        for label in ["🏠  Accueil", "🕘  Historique", "📝  Notes",
                      "📖  Dictionnaire", "⚡  Snippets", "📊  Statistiques",
                      "🎤  Réunion"]:
            it = QListWidgetItem(label)
            it.setSizeHint(QSize(0, 42))
            self.nav.addItem(it)
        lay.addWidget(self.nav)

        lay.addStretch(1)

        # Séparateur + bas de sidebar : « Réglages ».
        sep = QFrame(objectName="sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setContentsMargins(10, 0, 10, 0)
        lay.addWidget(sep)

        self.btn_settings = QPushButton("⚙   Réglages", objectName="settings")
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.clicked.connect(self._on_open_settings)
        lay.addWidget(self.btn_settings)
        lay.addSpacing(12)

        return sidebar

    def _make_logo(self):
        """Logo SVG (QSvgWidget) si disponible, sinon fallback emoji 🎙."""
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "voxaho-icon.svg",
        )
        if os.path.exists(icon_path):
            try:
                from PyQt6.QtSvgWidgets import QSvgWidget
                w = QSvgWidget(icon_path)
                w.setFixedSize(26, 26)
                return w
            except ImportError:
                pass
        fallback = QLabel("🎙")
        fallback.setStyleSheet("font-size: 20px;")
        return fallback

    # ── Helpers de construction ──────────────────────────────────────────────
    def _page(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        """Coquille de page : titre + sous-titre, marges cohérentes."""
        w = QWidget(objectName="page")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(14)
        lay.addWidget(QLabel(title, objectName="title"))
        if subtitle:
            lay.addWidget(QLabel(subtitle, objectName="subtitle"))
        lay.addSpacing(4)
        return w, lay

    def _section_label(self, text: str) -> QLabel:
        return QLabel(text, objectName="section")

    def _stat_card(self, value: str, label: str) -> tuple[QFrame, QLabel]:
        """Tuile de statistique #2C2C2E arrondie. Retourne (carte, label valeur)."""
        card = QFrame(objectName="card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(4)
        lb_val = QLabel(value, objectName="card-value")
        cl.addWidget(lb_val)
        cl.addWidget(QLabel(label, objectName="card-label"))
        return card, lb_val

    # ── Page 1 : Accueil (dashboard) ──────────────────────────────────────────
    def _build_dashboard_page(self) -> QWidget:
        w, lay = self._page("Bienvenue 👋", "Votre espace de dictée vocale locale")

        # Grille 2×2 de tuiles de stats.
        grid = QGridLayout()
        grid.setSpacing(14)
        self.dash_total, lb1 = self._stat_card("0", "Dictées au total")
        self.dash_words, lb2 = self._stat_card("0", "Mots dictés")
        self.dash_saved, lb3 = self._stat_card("0 min", "Temps économisé")
        self.dash_today, lb4 = self._stat_card("0", "Dictées aujourd'hui")
        self._dash_val_total = lb1
        self._dash_val_words = lb2
        self._dash_val_saved = lb3
        self._dash_val_today = lb4
        grid.addWidget(self.dash_total, 0, 0)
        grid.addWidget(self.dash_words, 0, 1)
        grid.addWidget(self.dash_saved, 1, 0)
        grid.addWidget(self.dash_today, 1, 1)
        lay.addLayout(grid)

        lay.addSpacing(8)
        lay.addWidget(self._section_label("Dernières dictées"))

        # Conteneur des 5 dernières dictées (reconstruit à chaque refresh).
        self._dash_recent = QWidget()
        self._dash_recent_lay = QVBoxLayout(self._dash_recent)
        self._dash_recent_lay.setContentsMargins(0, 0, 0, 0)
        self._dash_recent_lay.setSpacing(8)
        lay.addWidget(self._dash_recent)

        lay.addStretch(1)
        return w

    def _refresh_dashboard(self):
        stats = self._safe_call("stats", "get_stats", default=None)
        if stats is None:
            stats = {}
        self._dash_val_total.setText(str(stats.get("total_dictations", 0)))
        self._dash_val_words.setText(str(stats.get("total_words", 0)))
        self._dash_val_saved.setText(format_minutes(stats.get("time_saved_minutes", 0)))
        self._dash_val_today.setText(str(stats.get("today_dictations", 0)))

        # 5 dernières dictées.
        self._clear_layout(self._dash_recent_lay)
        entries = self._safe_call("history", "list_entries", default=None, limit=5)
        if entries is None:
            self._dash_recent_lay.addWidget(
                QLabel("Aucune donnée — l'historique n'est pas disponible.", objectName="empty"))
        elif not entries:
            self._dash_recent_lay.addWidget(
                QLabel("Aucune dictée pour le moment.", objectName="empty"))
        else:
            for e in entries[:5]:
                self._dash_recent_lay.addWidget(self._mini_entry(e))

    def _mini_entry(self, entry: dict) -> QWidget:
        """Ligne compacte (aperçu) d'une dictée pour le dashboard."""
        card = QFrame(objectName="entry")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(3)
        txt = QLabel(truncate_text(entry.get("text", ""), 90), objectName="entry-text")
        txt.setWordWrap(False)
        cl.addWidget(txt)
        meta = format_iso_datetime(entry.get("created_at", ""))
        lang = entry.get("language") or ""
        if lang:
            meta = f"{meta}  ·  {lang}"
        cl.addWidget(QLabel(meta, objectName="entry-meta"))
        return card

    # ── Page 2 : Historique ───────────────────────────────────────────────────
    def _build_history_page(self) -> QWidget:
        w, lay = self._page("Historique", "Vos dictées passées")

        # Barre d'outils : recherche + favoris.
        tools = QHBoxLayout()
        tools.setSpacing(10)
        self.hist_search = QLineEdit(objectName="search")
        self.hist_search.setPlaceholderText("🔍  Rechercher…")
        self.hist_search.setClearButtonEnabled(True)
        self.hist_search.textChanged.connect(self._reload_history)
        tools.addWidget(self.hist_search, 1)

        self.hist_fav_only = QCheckBox("⭐ Favoris seulement")
        self.hist_fav_only.stateChanged.connect(self._reload_history)
        tools.addWidget(self.hist_fav_only)
        lay.addLayout(tools)

        # Liste défilante de cartes.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._hist_container = QWidget()
        self._hist_lay = QVBoxLayout(self._hist_container)
        self._hist_lay.setContentsMargins(0, 0, 8, 0)
        self._hist_lay.setSpacing(8)
        self._hist_lay.addStretch(1)
        scroll.setWidget(self._hist_container)
        lay.addWidget(scroll, 1)

        # Barre du bas : toggle enregistrement + vider.
        sep = QFrame(objectName="sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(sep)

        bottom = QHBoxLayout()
        self.hist_enabled = QCheckBox("Enregistrer l'historique")
        self.hist_enabled.stateChanged.connect(self._on_history_enabled_toggle)
        bottom.addWidget(self.hist_enabled)
        bottom.addStretch(1)
        btn_clear = QPushButton("Vider l'historique", objectName="danger")
        btn_clear.clicked.connect(self._on_clear_history)
        bottom.addWidget(btn_clear)
        lay.addLayout(bottom)

        return w

    def _reload_history(self, *_):
        """Recharge la liste selon la recherche et le filtre favoris."""
        # Toggle « Enregistrer » : reflète l'état réel (défensif).
        enabled = self._safe_call("history", "is_enabled", default=None)
        self.hist_enabled.blockSignals(True)
        self.hist_enabled.setChecked(bool(enabled) if enabled is not None else False)
        self.hist_enabled.setEnabled(enabled is not None)
        self.hist_enabled.blockSignals(False)

        # Purge des cartes existantes (on conserve le stretch final).
        self._clear_layout(self._hist_lay, keep_last_stretch=True)

        search = self.hist_search.text().strip() or None
        fav = self.hist_fav_only.isChecked()
        entries = self._safe_call(
            "history", "list_entries", default=None,
            limit=200, search=search, favorites_only=fav,
        )

        if entries is None:
            self._insert_before_stretch(
                self._hist_lay, QLabel("Aucune donnée — historique indisponible.", objectName="empty"))
            return
        if not entries:
            msg = "Aucun résultat." if (search or fav) else "Aucune dictée enregistrée."
            self._insert_before_stretch(self._hist_lay, QLabel(msg, objectName="empty"))
            return

        for e in entries:
            self._insert_before_stretch(self._hist_lay, self._history_card(e))

    def _history_card(self, entry: dict) -> QWidget:
        """Carte d'une entrée d'historique avec actions (copier/favori/suppr.)."""
        eid = entry.get("id")
        card = QFrame(objectName="entry")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)

        # Ligne 1 : texte + boutons d'action.
        top = QHBoxLayout()
        top.setSpacing(6)
        txt = QLabel(truncate_text(entry.get("text", ""), 120), objectName="entry-text")
        txt.setWordWrap(True)
        top.addWidget(txt, 1)

        btn_copy = QPushButton("📋", objectName="icon")
        btn_copy.setToolTip("Copier le texte")
        btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_copy.clicked.connect(lambda _=False, t=entry.get("text", ""): self._copy_text(t))
        top.addWidget(btn_copy)

        is_fav = bool(entry.get("favorite"))
        btn_fav = QPushButton("⭐" if is_fav else "☆", objectName="icon")
        btn_fav.setToolTip("Favori")
        btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_fav.clicked.connect(lambda _=False, i=eid: self._toggle_favorite(i))
        top.addWidget(btn_fav)

        btn_del = QPushButton("🗑", objectName="icon")
        btn_del.setToolTip("Supprimer")
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.clicked.connect(lambda _=False, i=eid: self._delete_entry(i))
        top.addWidget(btn_del)
        cl.addLayout(top)

        # Ligne 2 : métadonnées (date · langue · modèle · mots).
        bits = [format_iso_datetime(entry.get("created_at", ""))]
        if entry.get("language"):
            bits.append(str(entry["language"]))
        if entry.get("model"):
            bits.append(str(entry["model"]))
        if entry.get("word_count"):
            bits.append(f"{entry['word_count']} mots")
        cl.addWidget(QLabel("  ·  ".join(b for b in bits if b), objectName="entry-meta"))
        return card

    def _on_history_enabled_toggle(self, *_):
        wanted = self.hist_enabled.isChecked()
        ok = self._safe_call("history", "set_enabled", default=None, _args=(wanted,))
        if ok is None:
            QMessageBox.warning(self, "Historique",
                                "Impossible de modifier ce réglage (module indisponible).")
        self._reload_history()

    def _on_clear_history(self):
        ans = QMessageBox.question(
            self, "Vider l'historique",
            "Supprimer définitivement toutes les dictées enregistrées ?",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._safe_call("history", "clear_all", default=None)
        self._reload_history()

    def _copy_text(self, text: str):
        try:
            QApplication.clipboard().setText(str(text or ""))
        except Exception as e:
            logger.warning("copie presse-papiers : %s", e)

    def _toggle_favorite(self, entry_id):
        if entry_id is None:
            return
        self._safe_call("history", "toggle_favorite", default=None, _args=(entry_id,))
        self._reload_history()

    def _delete_entry(self, entry_id):
        if entry_id is None:
            return
        self._safe_call("history", "delete_entry", default=None, _args=(entry_id,))
        self._reload_history()

    # ── Page 3 : Notes ────────────────────────────────────────────────────────
    def _build_notes_page(self) -> QWidget:
        w, lay = self._page("Notes", "Vos notes personnelles")

        split = QHBoxLayout()
        split.setSpacing(16)

        # Colonne gauche : bouton + liste des notes.
        left = QVBoxLayout()
        left.setSpacing(10)
        btn_new = QPushButton("+  Nouvelle note", objectName="primary")
        btn_new.clicked.connect(self._on_new_note)
        left.addWidget(btn_new)

        self.notes_list = QListWidget(objectName="notes-list")
        self.notes_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.notes_list.currentItemChanged.connect(self._on_note_selected)
        left.addWidget(self.notes_list, 1)
        left_w = QWidget()
        left_w.setFixedWidth(240)
        left_w.setLayout(left)
        split.addWidget(left_w)

        # Colonne droite : éditeur (titre + corps + actions).
        # Anti-GC : ces widgets sont conservés comme attributs d'instance.
        right = QVBoxLayout()
        right.setSpacing(10)
        self.note_title = QLineEdit()
        self.note_title.setPlaceholderText("Titre de la note")
        self.note_title.installEventFilter(self)  # auto-save sur perte de focus
        right.addWidget(self.note_title)

        self.note_body = QTextEdit()
        self.note_body.setPlaceholderText("Écrivez ici…")
        self.note_body.installEventFilter(self)
        right.addWidget(self.note_body, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        btn_del = QPushButton("Supprimer", objectName="danger")
        btn_del.clicked.connect(self._on_delete_note)
        actions.addWidget(btn_del)
        btn_save = QPushButton("Enregistrer", objectName="primary")
        btn_save.clicked.connect(self._autosave_note)
        actions.addWidget(btn_save)
        right.addLayout(actions)

        right_w = QWidget()
        right_w.setLayout(right)
        split.addWidget(right_w, 1)

        lay.addLayout(split, 1)
        self._set_note_editor_enabled(False)
        return w

    def _reload_notes(self):
        notes = self._safe_call("notes", "list_notes", default=None)
        self.notes_list.blockSignals(True)
        self.notes_list.clear()
        if notes:
            for n in notes:
                title = (n.get("title") or "").strip() or "(Sans titre)"
                it = QListWidgetItem(title)
                it.setData(Qt.ItemDataRole.UserRole, n.get("id"))
                self.notes_list.addItem(it)
        self.notes_list.blockSignals(False)

        # Restaure la sélection courante si la note existe encore.
        if self._current_note_id is not None:
            for i in range(self.notes_list.count()):
                it = self.notes_list.item(i)
                if it.data(Qt.ItemDataRole.UserRole) == self._current_note_id:
                    self.notes_list.setCurrentRow(i)
                    return
        # Sinon, vide l'éditeur.
        self._current_note_id = None
        self._clear_note_editor()

    def _on_new_note(self):
        new_id = self._safe_call("notes", "create_note", default=None,
                                 _args=("Nouvelle note", ""))
        if new_id is None:
            QMessageBox.warning(self, "Notes", "Impossible de créer la note (module indisponible).")
            return
        self._current_note_id = new_id
        self._reload_notes()
        self.note_title.setFocus()
        self.note_title.selectAll()

    def _on_note_selected(self, current, previous):
        # Sauver la note précédente avant de charger la nouvelle.
        if previous is not None:
            self._autosave_note()
        if current is None:
            self._current_note_id = None
            self._clear_note_editor()
            self._set_note_editor_enabled(False)
            return
        note_id = current.data(Qt.ItemDataRole.UserRole)
        self._current_note_id = note_id
        note = self._safe_call("notes", "get_note", default=None, _args=(note_id,))
        self._loading_note = True
        if note:
            self.note_title.setText(note.get("title", "") or "")
            self.note_body.setPlainText(note.get("body", "") or "")
        else:
            self._clear_note_editor()
        self._loading_note = False
        self._set_note_editor_enabled(True)

    def _autosave_note(self):
        """Sauve la note courante (perte de focus ou bouton Enregistrer)."""
        if self._loading_note or self._current_note_id is None:
            return
        title = self.note_title.text()
        body = self.note_body.toPlainText()
        self._safe_call("notes", "update_note", default=None,
                        _args=(self._current_note_id,), _kwargs={"title": title, "body": body})
        # Rafraîchit le libellé dans la liste sans tout reconstruire.
        it = self.notes_list.currentItem()
        if it is not None:
            it.setText((title or "").strip() or "(Sans titre)")

    def _on_delete_note(self):
        if self._current_note_id is None:
            return
        ans = QMessageBox.question(self, "Supprimer la note",
                                   "Supprimer définitivement cette note ?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._loading_note = True  # évite un auto-save parasite au changement de sélection
        self._safe_call("notes", "delete_note", default=None, _args=(self._current_note_id,))
        self._current_note_id = None
        self._loading_note = False
        self._reload_notes()

    def _clear_note_editor(self):
        self._loading_note = True
        self.note_title.clear()
        self.note_body.clear()
        self._loading_note = False

    def _set_note_editor_enabled(self, on: bool):
        self.note_title.setEnabled(on)
        self.note_body.setEnabled(on)

    # ── Page 4 : Dictionnaire ─────────────────────────────────────────────────
    def _build_dictionary_page(self) -> QWidget:
        w, lay = self._page(
            "Dictionnaire",
            "Vos termes, noms propres et jargon — mieux reconnus et corrigés "
            "automatiquement",
        )

        # Barre d'ajout : champ + bouton « + Ajouter » (en haut).
        # Anti-GC : conservé comme attribut d'instance.
        add_row = QHBoxLayout()
        add_row.setSpacing(10)
        self.dict_input = QLineEdit()
        self.dict_input.setPlaceholderText("Nouveau terme, nom propre, jargon…")
        self.dict_input.returnPressed.connect(self._on_add_term)
        add_row.addWidget(self.dict_input, 1)
        btn_add = QPushButton("+  Ajouter", objectName="primary")
        btn_add.clicked.connect(self._on_add_term)
        add_row.addWidget(btn_add)
        lay.addLayout(add_row)

        # Liste défilante des termes (cartes reconstruites à chaque refresh).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._dict_container = QWidget()
        self._dict_lay = QVBoxLayout(self._dict_container)
        self._dict_lay.setContentsMargins(0, 0, 8, 0)
        self._dict_lay.setSpacing(8)
        self._dict_lay.addStretch(1)
        scroll.setWidget(self._dict_container)
        lay.addWidget(scroll, 1)

        return w

    def _refresh_dictionary(self):
        """Recharge la liste des termes du dictionnaire (état vide géré)."""
        self._clear_layout(self._dict_lay, keep_last_stretch=True)
        terms = self._safe_call("dictionary", "list_terms", default=None)
        if terms is None:
            self._insert_before_stretch(
                self._dict_lay,
                QLabel("Aucun terme — dictionnaire indisponible.", objectName="empty"))
            return
        if not terms:
            self._insert_before_stretch(
                self._dict_lay,
                QLabel("Aucun terme pour le moment.", objectName="empty"))
            return
        for t in terms:
            self._insert_before_stretch(self._dict_lay, self._term_card(t))

    def _term_card(self, term: dict) -> QWidget:
        """Carte d'un terme : libellé + bouton supprimer (suppression directe)."""
        tid = term.get("id")
        card = QFrame(objectName="entry")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(6)
        lbl = QLabel(str(term.get("term", "")), objectName="entry-text")
        lbl.setWordWrap(True)
        cl.addWidget(lbl, 1)
        btn_del = QPushButton("🗑", objectName="icon")
        btn_del.setToolTip("Supprimer")
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.clicked.connect(lambda _=False, i=tid: self._delete_term(i))
        cl.addWidget(btn_del)
        return card

    def _on_add_term(self):
        term = self.dict_input.text().strip()
        if not term:
            return
        new_id = self._safe_call("dictionary", "add_term", default=None, _args=(term,))
        if new_id is None:
            QMessageBox.warning(self, "Dictionnaire",
                                "Impossible d'ajouter ce terme (module indisponible).")
            return
        self.dict_input.clear()
        self._refresh_dictionary()

    def _delete_term(self, term_id):
        # Items anodins : suppression directe, sans confirmation.
        if term_id is None:
            return
        self._safe_call("dictionary", "remove_term", default=None, _args=(term_id,))
        self._refresh_dictionary()

    # ── Page 5 : Snippets ─────────────────────────────────────────────────────
    def _build_snippets_page(self) -> QWidget:
        w, lay = self._page(
            "Snippets", "Dites un déclencheur, Voxaho écrit le texte complet")

        # Formulaire d'ajout / édition (carte). Anti-GC : attributs d'instance.
        form = QFrame(objectName="card")
        fl = QVBoxLayout(form)
        fl.setContentsMargins(16, 14, 16, 14)
        fl.setSpacing(10)
        self.snip_trigger = QLineEdit()
        self.snip_trigger.setPlaceholderText("Déclencheur (ex. « ma signature »)")
        fl.addWidget(self.snip_trigger)
        self.snip_expansion = QTextEdit()
        self.snip_expansion.setPlaceholderText("Texte complet à écrire…")
        self.snip_expansion.setFixedHeight(90)
        fl.addWidget(self.snip_expansion)

        form_actions = QHBoxLayout()
        form_actions.addStretch(1)
        self.snip_cancel = QPushButton("Annuler", objectName="ghost")
        self.snip_cancel.clicked.connect(self._cancel_snippet_edit)
        self.snip_cancel.setVisible(False)  # visible seulement en mode édition
        form_actions.addWidget(self.snip_cancel)
        self.snip_add_btn = QPushButton("+  Ajouter", objectName="primary")
        self.snip_add_btn.clicked.connect(self._on_save_snippet)
        form_actions.addWidget(self.snip_add_btn)
        fl.addLayout(form_actions)
        lay.addWidget(form)

        # Liste défilante des snippets (reconstruite à chaque refresh).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._snip_container = QWidget()
        self._snip_lay = QVBoxLayout(self._snip_container)
        self._snip_lay.setContentsMargins(0, 0, 8, 0)
        self._snip_lay.setSpacing(8)
        self._snip_lay.addStretch(1)
        scroll.setWidget(self._snip_container)
        lay.addWidget(scroll, 1)

        return w

    def _refresh_snippets(self):
        """Recharge la liste des snippets (état vide géré)."""
        self._clear_layout(self._snip_lay, keep_last_stretch=True)
        snippets = self._safe_call("snippets", "list_snippets", default=None)
        if snippets is None:
            self._insert_before_stretch(
                self._snip_lay,
                QLabel("Aucun snippet — module indisponible.", objectName="empty"))
            return
        if not snippets:
            self._insert_before_stretch(
                self._snip_lay,
                QLabel("Aucun snippet pour le moment.", objectName="empty"))
            return
        for s in snippets:
            self._insert_before_stretch(self._snip_lay, self._snippet_card(s))

    def _snippet_card(self, snippet: dict) -> QWidget:
        """Carte d'un snippet : « trigger → expansion » tronqué + éditer/supprimer."""
        sid = snippet.get("id")
        card = QFrame(objectName="entry")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(6)
        lbl = QLabel(
            snippet_preview(snippet.get("trigger", ""), snippet.get("expansion", "")),
            objectName="entry-text")
        lbl.setWordWrap(True)
        cl.addWidget(lbl, 1)

        btn_edit = QPushButton("✏️", objectName="icon")
        btn_edit.setToolTip("Éditer")
        btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_edit.clicked.connect(lambda _=False, s=snippet: self._edit_snippet(s))
        cl.addWidget(btn_edit)

        btn_del = QPushButton("🗑", objectName="icon")
        btn_del.setToolTip("Supprimer")
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.clicked.connect(lambda _=False, i=sid: self._delete_snippet(i))
        cl.addWidget(btn_del)
        return card

    def _on_save_snippet(self):
        """Ajoute (mode ajout) ou met à jour (mode édition) le snippet du formulaire."""
        trigger = self.snip_trigger.text().strip()
        expansion = self.snip_expansion.toPlainText().strip()
        if not trigger or not expansion:
            return
        if self._editing_snippet_id is not None:
            self._safe_call(
                "snippets", "update_snippet", default=None,
                _args=(self._editing_snippet_id,),
                _kwargs={"trigger": trigger, "expansion": expansion})
        else:
            new_id = self._safe_call(
                "snippets", "add_snippet", default=None, _args=(trigger, expansion))
            if new_id is None:
                QMessageBox.warning(self, "Snippets",
                                    "Impossible d'ajouter ce snippet (module indisponible).")
                return
        self._cancel_snippet_edit()  # réinitialise le formulaire + le mode
        self._refresh_snippets()

    def _edit_snippet(self, snippet: dict):
        """Recharge un snippet dans le formulaire (passe en mode édition)."""
        self._editing_snippet_id = snippet.get("id")
        self.snip_trigger.setText(str(snippet.get("trigger", "") or ""))
        self.snip_expansion.setPlainText(str(snippet.get("expansion", "") or ""))
        self.snip_add_btn.setText("Enregistrer")
        self.snip_cancel.setVisible(True)
        self.snip_trigger.setFocus()

    def _cancel_snippet_edit(self):
        """Réinitialise le formulaire et repasse en mode ajout."""
        self._editing_snippet_id = None
        self.snip_trigger.clear()
        self.snip_expansion.clear()
        self.snip_add_btn.setText("+  Ajouter")
        self.snip_cancel.setVisible(False)

    def _delete_snippet(self, snippet_id):
        if snippet_id is None:
            return
        self._safe_call("snippets", "remove_snippet", default=None, _args=(snippet_id,))
        # Si on supprimait le snippet en cours d'édition, on vide le formulaire.
        if self._editing_snippet_id == snippet_id:
            self._cancel_snippet_edit()
        self._refresh_snippets()

    # ── Page 6 : Statistiques ─────────────────────────────────────────────────
    def _build_stats_page(self) -> QWidget:
        w, lay = self._page("Statistiques", "Votre activité de dictée")

        grid = QGridLayout()
        grid.setSpacing(14)
        specs = [
            ("total_dictations", "Dictées au total"),
            ("total_words", "Mots dictés"),
            ("total_chars", "Caractères"),
            ("avg_words_per_dictation", "Mots / dictée (moy.)"),
        ]
        self._stats_labels = {}
        for i, (key, label) in enumerate(specs):
            card, lb = self._stat_card("0", label)
            self._stats_labels[key] = lb
            grid.addWidget(card, i // 2, i % 2)
        lay.addLayout(grid)

        lay.addSpacing(8)
        lay.addWidget(self._section_label("Mots par jour (30 derniers jours)"))
        self._stats_chart = _MiniBarChart()
        lay.addWidget(self._stats_chart)

        self._stats_footer = QLabel("", objectName="desc")
        self._stats_footer.setWordWrap(True)
        lay.addWidget(self._stats_footer)

        lay.addStretch(1)
        return w

    def _refresh_stats(self):
        stats = self._safe_call("stats", "get_stats", default=None)
        if stats is None:
            for lb in self._stats_labels.values():
                lb.setText("—")
            self._stats_chart.set_data([])
            self._stats_footer.setText("Aucune donnée — statistiques indisponibles.")
            return

        for key, lb in self._stats_labels.items():
            val = stats.get(key, 0)
            if key == "avg_words_per_dictation":
                try:
                    lb.setText(f"{float(val):.1f}")
                except (TypeError, ValueError):
                    lb.setText("0")
            else:
                lb.setText(str(val))

        series = self._safe_call("stats", "words_per_day", default=None, _args=(30,))
        self._stats_chart.set_data(series or [])

        # Pied : temps économisé + première utilisation.
        parts = [f"Temps économisé : {format_minutes(stats.get('time_saved_minutes', 0))}"]
        first = stats.get("first_use_date")
        if first:
            parts.append(f"Première utilisation : {format_iso_datetime(first)}")
        self._stats_footer.setText("     ·     ".join(parts))

    # ── Page 7 : Réunion (transcription continue horodatée) ───────────────────
    def _build_meeting_page(self) -> QWidget:
        w, lay = self._page(
            "Réunion",
            "Transcription continue, horodatée et 100 % locale — idéale pour vos "
            "réunions et prises de notes longues")

        # Barre d'action : bouton démarrer/arrêter + libellé d'état.
        # Anti-GC : conservés comme attributs d'instance.
        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.meeting_btn = QPushButton("●  Démarrer la réunion", objectName="primary")
        self.meeting_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.meeting_btn.clicked.connect(self._on_toggle_meeting)
        controls.addWidget(self.meeting_btn)
        self.meeting_status = QLabel("", objectName="desc")
        controls.addWidget(self.meeting_status, 1)
        lay.addLayout(controls)

        # Message d'erreur (module/micro indisponible) — masqué par défaut.
        self.meeting_error = QLabel("", objectName="empty")
        self.meeting_error.setWordWrap(True)
        self.meeting_error.setVisible(False)
        lay.addWidget(self.meeting_error)

        # Zone de transcript live défilante (lecture seule ; auto-scroll à l'ajout).
        self.meeting_transcript = QTextEdit()
        self.meeting_transcript.setReadOnly(True)
        self.meeting_transcript.setPlaceholderText(
            "Le transcript s'affichera ici, horodaté, au fil de la réunion…")
        lay.addWidget(self.meeting_transcript, 1)

        # Actions de fin : export Markdown + enregistrement en note (désactivées
        # tant qu'il n'y a pas de contenu à exporter).
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.meeting_export_btn = QPushButton("Exporter en Markdown", objectName="ghost")
        self.meeting_export_btn.clicked.connect(self._export_meeting_markdown)
        self.meeting_export_btn.setEnabled(False)
        actions.addWidget(self.meeting_export_btn)
        self.meeting_note_btn = QPushButton("Enregistrer comme note", objectName="primary")
        self.meeting_note_btn.clicked.connect(self._save_meeting_as_note)
        self.meeting_note_btn.setEnabled(False)
        actions.addWidget(self.meeting_note_btn)
        lay.addLayout(actions)

        return w

    def _meeting_available(self) -> tuple[bool, str]:
        """Vérifie que le moteur Réunion et ses dépendances sont disponibles.

        Retourne (ok, message). Défensif : ne lève jamais. Contrôle la présence
        du module core.meeting, de sounddevice (capture) et de faster-whisper
        (transcription) sans les charger réellement.
        """
        if _mod("meeting") is None:
            return False, "Le mode Réunion est indisponible (module manquant)."
        import importlib.util
        try:
            if importlib.util.find_spec("sounddevice") is None:
                return False, "Micro indisponible : le module « sounddevice » n'est pas installé."
            if importlib.util.find_spec("faster_whisper") is None:
                return False, "Transcription indisponible : « faster-whisper » n'est pas installé."
        except Exception as e:
            return False, f"Mode Réunion indisponible : {e}"
        return True, ""

    def _refresh_meeting(self):
        """Réaligne l'état de la page Réunion (bouton, message d'erreur).

        N'interrompt jamais une session en cours (navigation aller/retour) : si la
        réunion tourne, on conserve simplement l'état « Arrêter ».
        """
        running = self._meeting_session is not None and self._meeting_session.is_running()
        if running:
            self.meeting_error.setVisible(False)
            self.meeting_btn.setEnabled(True)
            self.meeting_btn.setText("■  Arrêter")
            self.meeting_status.setText("● Enregistrement en cours…")
            return

        self.meeting_btn.setText("●  Démarrer la réunion")
        self.meeting_status.setText("")
        ok, msg = self._meeting_available()
        self.meeting_btn.setEnabled(ok)
        self.meeting_error.setText("" if ok else msg)
        self.meeting_error.setVisible(not ok)
        has = bool(self._meeting_segments)
        self.meeting_export_btn.setEnabled(has)
        self.meeting_note_btn.setEnabled(has)

    def _on_toggle_meeting(self):
        if self._meeting_session is not None and self._meeting_session.is_running():
            self._stop_meeting()
        else:
            self._start_meeting()

    def _start_meeting(self):
        ok, msg = self._meeting_available()
        if not ok:
            self._show_meeting_error(msg)
            return
        try:
            from core.meeting import MeetingSession
        except Exception as e:
            self._show_meeting_error(f"Le mode Réunion est indisponible : {e}")
            return

        # Réinitialise l'affichage et l'accumulateur de segments.
        self._meeting_segments = []
        self.meeting_transcript.clear()
        self.meeting_export_btn.setEnabled(False)
        self.meeting_note_btn.setEnabled(False)

        try:
            self._meeting_session = MeetingSession()
            # Le callback émet un signal Qt → remontée thread-safe vers le slot.
            self._meeting_session.start(on_segment=self.meeting_segment.emit)
        except Exception as e:  # MeetingError (micro indispo) ou toute autre erreur
            self._meeting_session = None
            self._show_meeting_error(f"Impossible de démarrer la capture audio : {e}")
            return

        self.meeting_error.setVisible(False)
        self.meeting_btn.setText("■  Arrêter")
        self.meeting_status.setText("● Enregistrement en cours…")

    def _stop_meeting(self):
        session = self._meeting_session
        self._meeting_session = None
        if session is not None:
            try:
                # La liste retournée fait foi (complète, ordonnée, inclut le flush final).
                segments = session.stop()
                if segments is not None:
                    self._meeting_segments = segments
            except Exception as e:
                logger.warning("Réunion : arrêt de la session : %s", e)

        self.meeting_btn.setText("●  Démarrer la réunion")
        self.meeting_status.setText("Réunion terminée." if self._meeting_segments else "")
        has = bool(self._meeting_segments)
        self.meeting_export_btn.setEnabled(has)
        self.meeting_note_btn.setEnabled(has)

    def _on_meeting_segment(self, segment: dict):
        """Slot connecté à meeting_segment : append un segment au transcript live."""
        try:
            self._meeting_segments.append(segment)
            ts = segment.get("timestamp", "")
            text = str(segment.get("text", "")).strip()
            self.meeting_transcript.append(f"[{ts}]  {text}")
        except Exception as e:  # jamais crasher l'UI sur un segment
            logger.warning("Réunion : affichage d'un segment : %s", e)

    def _export_meeting_markdown(self):
        if not self._meeting_segments:
            return
        try:
            from core.meeting import export_markdown
            md = export_markdown(self._meeting_segments)
        except Exception as e:
            QMessageBox.warning(self, "Réunion", f"Export impossible : {e}")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter la réunion", "reunion.md", "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
        except OSError as e:
            QMessageBox.warning(self, "Réunion", f"Écriture impossible : {e}")

    def _save_meeting_as_note(self):
        if not self._meeting_segments:
            return
        try:
            from core.meeting import merge_segments
            body = merge_segments(self._meeting_segments)
        except Exception as e:
            logger.warning("Réunion : fusion des segments : %s", e)
            body = ""
        title = f"Réunion du {format_iso_datetime(datetime.now().isoformat())}"
        new_id = self._safe_call("notes", "create_note", default=None, _args=(title, body))
        if new_id is None:
            QMessageBox.warning(
                self, "Réunion",
                "Impossible d'enregistrer la note (module indisponible).")
            return
        QMessageBox.information(self, "Réunion", "Réunion enregistrée dans vos notes.")

    def _show_meeting_error(self, message: str):
        self.meeting_error.setText(message)
        self.meeting_error.setVisible(True)
        self.meeting_btn.setText("●  Démarrer la réunion")
        self.meeting_status.setText("")

    # ── Rafraîchissement / navigation ─────────────────────────────────────────
    def refresh(self):
        """Recharge la page courante (appelée par l'intégration via dictation_added)."""
        idx = self.stack.currentIndex()
        try:
            if idx == self.PAGE_HOME:
                self._refresh_dashboard()
            elif idx == self.PAGE_HISTORY:
                self._reload_history()
            elif idx == self.PAGE_NOTES:
                self._reload_notes()
            elif idx == self.PAGE_DICTIONARY:
                self._refresh_dictionary()
            elif idx == self.PAGE_SNIPPETS:
                self._refresh_snippets()
            elif idx == self.PAGE_STATS:
                self._refresh_stats()
            elif idx == self.PAGE_MEETING:
                self._refresh_meeting()
        except Exception as e:  # jamais crasher la fenêtre sur un refresh
            logger.warning("refresh(page=%s) : %s", idx, e)

    def _on_nav_changed(self, row: int):
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        self.refresh()

    def _on_open_settings(self):
        if callable(self._open_settings):
            try:
                self._open_settings()
            except Exception as e:
                logger.warning("open_settings_fn : %s", e)
        else:
            QMessageBox.information(
                self, "Réglages",
                "Les préférences s'ouvriront ici une fois l'intégration branchée.")

    def showEvent(self, event):
        # Rafraîchit la page visible à chaque affichage de la fenêtre.
        super().showEvent(event)
        self.refresh()

    def eventFilter(self, obj, event):
        # Auto-save de la note quand le titre ou le corps perd le focus.
        if event.type() == QEvent.Type.FocusOut and obj in (
                getattr(self, "note_title", None), getattr(self, "note_body", None)):
            self._autosave_note()
        return super().eventFilter(obj, event)

    # ── Utilitaires ───────────────────────────────────────────────────────────
    def _safe_call(self, mod_name: str, func_name: str, *, default=None,
                   _args=(), _kwargs=None, **kwargs):
        """Appelle core.<mod>.<func>(...) défensivement ; retourne `default` en cas d'échec.

        Sépare les positionnels/nommés « métier » (_args/_kwargs) des kwargs
        passés directement pour un confort d'appel (ex. limit=5).
        """
        mod = _mod(mod_name)
        if mod is None:
            return default
        fn = getattr(mod, func_name, None)
        if not callable(fn):
            return default
        call_kwargs = dict(_kwargs or {})
        call_kwargs.update(kwargs)
        try:
            return fn(*_args, **call_kwargs)
        except Exception as e:
            logger.warning("core.%s.%s : %s", mod_name, func_name, e)
            return default

    def _clear_layout(self, layout, keep_last_stretch: bool = False):
        """Supprime tous les widgets d'un layout (optionnellement garde le stretch final)."""
        count = layout.count()
        stop = count - 1 if keep_last_stretch else count
        for i in reversed(range(stop)):
            item = layout.takeAt(i)
            wdg = item.widget()
            if wdg is not None:
                wdg.setParent(None)
                wdg.deleteLater()

    def _insert_before_stretch(self, layout, widget):
        """Insère un widget juste avant le stretch final du layout."""
        layout.insertWidget(layout.count() - 1, widget)
