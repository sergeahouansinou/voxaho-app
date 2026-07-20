"""
Tests du mode « Réunion » — PHASE 3 « Au-delà de Wispr ».

Deux volets, SANS audio réel et SANS QApplication :

1. core.meeting — fonctions PURES (format_timestamp, export_markdown,
   merge_segments) et la LOGIQUE de MeetingSession. La capture audio et la
   transcription live sont isolées derrière `_open_stream` et `_transcribe_chunk`
   (mockés ici) : aucun micro n'est jamais ouvert.

2. ui/workspace_window.py — via ast.parse + lecture du source, on vérifie la
   présence de la section « Réunion », la synchro nav ⇄ stack ⇄ PAGE_*, le signal
   thread-safe et les méthodes d'intégration. Aucun widget n'est instancié.

Lancer : ./venv/bin/python -m pytest tests/test_meeting.py -v
"""

import ast
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import meeting  # noqa: E402  (après l'ajout du root au sys.path)
from core.meeting import MeetingSession, MeetingError

WORKSPACE_PATH = PROJECT_ROOT / "ui" / "workspace_window.py"


# ── Doublures de test (pas de micro, pas de modèle Whisper) ───────────────────

class _DummyTranscriber:
    """Transcriber factice : preload/transcribe inertes (aucun modèle chargé)."""

    def __init__(self):
        self.preloaded = False

    def preload(self):
        self.preloaded = True

    def transcribe(self, audio):
        return ""


class _DummyStream:
    """Flux sounddevice factice : trace les appels stop()/close()."""

    def __init__(self):
        self.stopped = False
        self.closed = False

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def _chunk(seconds: float) -> np.ndarray:
    """Faux chunk audio d'une durée donnée (silence float32 16 kHz)."""
    return np.zeros(int(MeetingSession.SAMPLE_RATE * seconds), dtype=np.float32)


# ── format_timestamp ──────────────────────────────────────────────────────────

def test_format_timestamp_valeurs_de_reference():
    assert meeting.format_timestamp(0) == "00:00"
    assert meeting.format_timestamp(65) == "01:05"
    assert meeting.format_timestamp(3661) == "01:01:01"


def test_format_timestamp_tronque_et_robuste():
    # Fractions tronquées, valeurs négatives / non numériques → "00:00".
    assert meeting.format_timestamp(65.9) == "01:05"
    assert meeting.format_timestamp(-10) == "00:00"
    assert meeting.format_timestamp(None) == "00:00"
    assert meeting.format_timestamp("bof") == "00:00"


def test_format_timestamp_passage_a_lheure():
    # 3600 s pile → format HH:MM:SS ; 3599 s → reste MM:SS.
    assert meeting.format_timestamp(3599) == "59:59"
    assert meeting.format_timestamp(3600) == "01:00:00"


# ── export_markdown ─────────────────────────────────────────────────────────

def test_export_markdown_liste_vide_titre_seul():
    md = meeting.export_markdown([])
    assert "# Réunion" in md
    assert "- **[" not in md  # aucune ligne de segment


def test_export_markdown_titre_personnalise():
    md = meeting.export_markdown([], title="Comité de pilotage")
    assert "# Comité de pilotage" in md


def test_export_markdown_lignes_horodatees():
    segs = [
        {"t": 0.0, "timestamp": "00:00", "text": "Bonjour à tous."},
        {"t": 6.0, "timestamp": "00:06", "text": "On démarre."},
    ]
    md = meeting.export_markdown(segs)
    assert "- **[00:00]** Bonjour à tous." in md
    assert "- **[06:00]** On démarre." not in md  # le timestamp du segment fait foi
    assert "- **[00:06]** On démarre." in md


def test_export_markdown_ignore_segments_vides_ou_malformes():
    segs = [
        {"t": 0.0, "timestamp": "00:00", "text": "  "},  # texte vide → ignoré
        "pas un dict",                                     # malformé → ignoré
        {"t": 1.0, "timestamp": "00:01", "text": "Contenu."},
    ]
    md = meeting.export_markdown(segs)
    assert md.count("- **[") == 1
    assert "- **[00:01]** Contenu." in md


# ── merge_segments ────────────────────────────────────────────────────────────

def test_merge_segments_concatene():
    segs = [
        {"text": "Première phrase."},
        {"text": "Deuxième phrase."},
    ]
    assert meeting.merge_segments(segs) == "Première phrase. Deuxième phrase."


def test_merge_segments_robuste():
    assert meeting.merge_segments([]) == ""
    assert meeting.merge_segments(None) == ""
    segs = [{"text": "  Utile.  "}, {"text": ""}, "malformé", {"autre": 1}]
    assert meeting.merge_segments(segs) == "Utile."


# ── MeetingSession : logique (sans audio) ─────────────────────────────────────

def test_meeting_session_accumule_segments_horodates():
    session = MeetingSession(window_seconds=0.05, transcriber=_DummyTranscriber())
    dummy_stream = _DummyStream()
    session._open_stream = lambda cb: dummy_stream  # aucun micro

    textes = iter(["Bonjour à tous.", "", "On commence la réunion.", "Point suivant."])
    session._transcribe_chunk = lambda audio: next(textes)

    recu = []
    session.start(on_segment=recu.append)
    assert session.is_running()

    # Injection de 4 faux chunks de 6 s (un chunk « vide » ne produit pas de segment).
    for _ in range(4):
        session._add_segment_from_chunk(_chunk(6))

    segments = session.stop()
    assert not session.is_running()

    # 4 chunks, 1 sans texte → 3 segments.
    assert len(segments) == 3
    assert segments == session.segments  # stop() renvoie bien la liste courante

    # on_segment appelé une fois par segment NON vide.
    assert recu == segments

    # Timestamps strictement croissants, calés sur la durée d'audio consommée
    # (chunk 0 à t=0, le 3e chunk à t=12, le 4e à t=18 — le chunk vide compte
    # dans l'horloge mais ne crée pas de segment).
    ts = [s["t"] for s in segments]
    assert ts == [0.0, 12.0, 18.0]
    assert all(a < b for a, b in zip(ts, ts[1:]))
    assert segments[0]["timestamp"] == "00:00"
    assert segments[1]["timestamp"] == "00:12"

    # Le flux a bien été arrêté et fermé.
    assert dummy_stream.stopped and dummy_stream.closed


def test_meeting_session_segment_dict_complet():
    session = MeetingSession(transcriber=_DummyTranscriber())
    session._open_stream = lambda cb: _DummyStream()
    session._transcribe_chunk = lambda audio: "Une phrase."
    session.start()
    seg = session._add_segment_from_chunk(_chunk(6))
    session.stop()
    assert set(seg.keys()) == {"t", "timestamp", "text"}
    assert isinstance(seg["t"], float)
    assert seg["text"] == "Une phrase."
    assert seg["timestamp"] == "00:00"


def test_meeting_session_callback_exception_ne_crashe_pas():
    session = MeetingSession(transcriber=_DummyTranscriber())
    session._open_stream = lambda cb: _DummyStream()
    session._transcribe_chunk = lambda audio: "texte"

    def bad_callback(seg):
        raise ValueError("boom")

    session.start(on_segment=bad_callback)
    # L'exception du callback est absorbée : le segment est tout de même enregistré.
    session._add_segment_from_chunk(_chunk(6))
    assert len(session.segments) == 1
    session.stop()


class _BoomTranscriber:
    """Transcriber factice dont transcribe() lève systématiquement."""

    def preload(self):
        pass

    def transcribe(self, audio):
        raise RuntimeError("modèle KO")


def test_meeting_session_transcription_exception_absorbee():
    """Une transcription qui lève ne crashe pas la session (chunk ignoré).

    On passe par le VRAI `_transcribe_chunk` (non mocké) avec un transcriber qui
    lève : l'erreur doit être absorbée (retour "") et aucun segment créé.
    """
    session = MeetingSession(transcriber=_BoomTranscriber())
    session._open_stream = lambda cb: _DummyStream()
    session.start()
    session._add_segment_from_chunk(_chunk(6))
    assert session.segments == []  # aucun segment, pas d'exception remontée
    session.stop()


def test_meeting_session_start_erreur_flux_leve_meeting_error():
    session = MeetingSession(transcriber=_DummyTranscriber())

    def open_ko(cb):
        raise OSError("aucun périphérique d'entrée")

    session._open_stream = open_ko
    with pytest.raises(MeetingError):
        session.start()
    assert not session.is_running()  # l'état est proprement réinitialisé


def test_meeting_session_stop_sans_start():
    session = MeetingSession(transcriber=_DummyTranscriber())
    assert session.stop() == []
    assert not session.is_running()


def test_meeting_session_double_start_ignore():
    session = MeetingSession(transcriber=_DummyTranscriber())
    session._open_stream = lambda cb: _DummyStream()
    session._transcribe_chunk = lambda audio: ""
    session.start()
    assert session.is_running()
    session.start()  # second start ignoré, ne doit pas lever
    assert session.is_running()
    session.stop()


# ── Intégration UI (ast.parse + source, sans widget) ──────────────────────────

def _source() -> str:
    return WORKSPACE_PATH.read_text(encoding="utf-8")


def _import_workspace():
    try:
        import ui.workspace_window as w
    except ImportError as e:  # PyQt6 absent : on saute proprement
        pytest.skip(f"PyQt6 indisponible : {e}")
    return w


def test_workspace_syntaxe_valide():
    ast.parse(_source())  # lève SyntaxError si invalide


def test_workspace_section_reunion_presente():
    src = _source()
    assert "🎤  Réunion" in src, "item de navigation « Réunion » manquant"
    assert "_build_meeting_page" in src


def test_workspace_page_meeting_apres_statistiques():
    """La page Réunion est ajoutée au stack APRÈS Statistiques (index 6)."""
    src = _source()
    i_stats = src.index("self.stack.addWidget(self._build_stats_page(")
    i_meet = src.index("self.stack.addWidget(self._build_meeting_page(")
    assert i_stats < i_meet


def test_workspace_constantes_page_a_jour():
    """PAGE_MEETING == 6 et les index existants restent inchangés (pas de régression)."""
    w = _import_workspace()
    assert w.WorkspaceWindow.PAGE_STATS == 5
    assert w.WorkspaceWindow.PAGE_MEETING == 6


def test_workspace_signal_meeting_segment():
    """Le signal thread-safe meeting_segment(dict) est déclaré."""
    assert "meeting_segment = pyqtSignal(dict)" in _source()


def test_workspace_methodes_meeting_presentes():
    w = _import_workspace()
    for m in ("_build_meeting_page", "_refresh_meeting", "_on_toggle_meeting",
              "_start_meeting", "_stop_meeting", "_on_meeting_segment",
              "_export_meeting_markdown", "_save_meeting_as_note"):
        assert hasattr(w.WorkspaceWindow, m), f"méthode manquante : {m}"


def test_workspace_refresh_gere_page_meeting():
    src = _source()
    assert "self.PAGE_MEETING" in src
    assert "self._refresh_meeting()" in src


def test_workspace_appelle_moteur_meeting():
    """L'UI référence le moteur autonome core.meeting (import défensif)."""
    src = _source()
    assert "from core.meeting import MeetingSession" in src
    assert "export_markdown" in src
    assert "merge_segments" in src
