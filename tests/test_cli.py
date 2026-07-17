"""
Tests de la CLI locale (core.cli) — SANS vrai modèle Whisper.

Principes :
  - On patche les attributs des VRAIS modules (monkeypatch.setattr sur l'objet
    module), jamais via sys.modules[...] = ... : le test reste robuste à l'ordre
    d'exécution et n'empoisonne pas les imports des autres tests.
  - Aucune dépendance lourde : Transcriber.transcribe est monkeypatché pour
    renvoyer un texte simulé, donc faster-whisper n'est jamais chargé.
  - stdout est vérifié tel quel (contrat « script-friendly » : résultat pur sur
    stdout, messages/erreurs sur stderr).

Lancement : ./venv/bin/python -m pytest tests/test_cli.py -v
"""

import json
import re
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

from core import cli


# ── Helpers ──────────────────────────────────────────────────────────────────

def _write_wav(path, *, samplerate=16000, channels=1, seconds=0.1):
    """Fabrique un petit WAV PCM 16 bits pour les tests (contenu quelconque)."""
    n = int(samplerate * seconds)
    # Un signal quelconque (le contenu n'importe pas : transcribe est patché).
    mono = (np.sin(np.linspace(0, 6.28, n)) * 1000).astype("<i2")
    if channels == 1:
        data = mono
    else:
        data = np.repeat(mono[:, None], channels, axis=1).reshape(-1)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(data.astype("<i2").tobytes())
    return path


# ── Parsing argparse : présence des sous-commandes et options ────────────────

def test_parser_transcribe_options():
    parser = cli._build_parser()
    args = parser.parse_args([
        "transcribe", "a.wav",
        "--model", "medium", "--language", "en",
        "--no-reformat", "--translate-to", "es",
    ])
    assert args.command == "transcribe"
    assert args.file == "a.wav"
    assert args.model == "medium"
    assert args.language == "en"
    assert args.no_reformat is True
    assert args.translate_to == "es"
    assert args.func is cli._cmd_transcribe


def test_parser_transcribe_defauts():
    parser = cli._build_parser()
    args = parser.parse_args(["transcribe", "a.wav"])
    assert args.model == "small"
    assert args.language == "fr"
    assert args.no_reformat is False
    assert args.translate_to is None


def test_parser_history_options():
    parser = cli._build_parser()
    args = parser.parse_args([
        "history", "--limit", "5", "--search", "facture", "--favorites", "--json",
    ])
    assert args.command == "history"
    assert args.limit == 5
    assert args.search == "facture"
    assert args.favorites is True
    assert args.json is True
    assert args.func is cli._cmd_history


def test_parser_stats_et_version():
    parser = cli._build_parser()
    args = parser.parse_args(["stats", "--json"])
    assert args.command == "stats"
    assert args.json is True
    assert args.func is cli._cmd_stats

    args = parser.parse_args(["version"])
    assert args.command == "version"
    assert args.func is cli._cmd_version


def test_help_subcommandes(capsys):
    """--help de chaque sous-commande fonctionne (SystemExit code 0)."""
    parser = cli._build_parser()
    for sub in ("transcribe", "history", "stats", "version"):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args([sub, "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert sub in out or "usage" in out.lower()


# ── version ──────────────────────────────────────────────────────────────────

def test_version_imprime_semver_reel(capsys):
    """La vraie version est imprimée et respecte le format semver X.Y.Z."""
    code = cli.main(["version"])
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert re.match(r"^\d+\.\d+\.\d+$", out), f"version inattendue : {out!r}"


def test_version_monkeypatch(capsys, monkeypatch):
    """La version affichée suit core.__version__ (patch de l'attribut réel)."""
    import core
    monkeypatch.setattr(core, "__version__", "9.9.9")
    code = cli.main(["version"])
    assert code == 0
    assert capsys.readouterr().out.strip() == "9.9.9"


# ── history ──────────────────────────────────────────────────────────────────

_FAKE_ENTRIES = [
    {
        "id": 2, "text": "Deuxième dictée", "created_at": "2026-07-17T10:00:00+00:00",
        "language": "fr", "model": "small", "word_count": 2, "favorite": True,
    },
    {
        "id": 1, "text": "Première dictée", "created_at": "2026-07-16T09:00:00+00:00",
        "language": "en", "model": "medium", "word_count": 2, "favorite": False,
    },
]


def test_history_texte(capsys, monkeypatch):
    from core import history
    captured = {}

    def fake_list_entries(*, limit, search, favorites_only):
        captured.update(limit=limit, search=search, favorites_only=favorites_only)
        return _FAKE_ENTRIES

    monkeypatch.setattr(history, "list_entries", fake_list_entries)

    code = cli.main(["history", "--limit", "5", "--search", "dict", "--favorites"])
    assert code == 0
    # Les options sont bien transmises au module historique.
    assert captured == {"limit": 5, "search": "dict", "favorites_only": True}

    out = capsys.readouterr().out
    assert "Deuxième dictée" in out
    assert "Première dictée" in out
    assert "[2]" in out and "[1]" in out
    assert "★" in out  # la 1ʳᵉ entrée est favorite


def test_history_json_roundtrip(capsys, monkeypatch):
    from core import history
    monkeypatch.setattr(history, "list_entries", lambda **kw: _FAKE_ENTRIES)

    code = cli.main(["history", "--json"])
    assert code == 0
    parsed = json.loads(capsys.readouterr().out)  # round-trip JSON
    assert isinstance(parsed, list) and len(parsed) == 2
    assert parsed[0]["text"] == "Deuxième dictée"
    assert parsed[0]["favorite"] is True


def test_history_vide_texte(capsys, monkeypatch):
    from core import history
    monkeypatch.setattr(history, "list_entries", lambda **kw: [])
    code = cli.main(["history"])
    assert code == 0
    assert "Aucune dictée" in capsys.readouterr().out


# ── stats ──────────────────────────────────────────────────────────────────────

_FAKE_STATS = {
    "total_dictations": 3, "total_words": 42, "total_chars": 210,
    "avg_words_per_dictation": 14.0, "today_dictations": 1, "today_words": 12,
    "first_use_date": "2026-01-01T00:00:00+00:00", "time_saved_minutes": 1.7,
}


def test_stats_texte(capsys, monkeypatch):
    from core import stats
    monkeypatch.setattr(stats, "get_stats", lambda: _FAKE_STATS)
    code = cli.main(["stats"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Statistiques Voxaho" in out
    assert "42" in out  # total de mots
    assert "1.7" in out  # temps gagné


def test_stats_json_roundtrip(capsys, monkeypatch):
    from core import stats
    monkeypatch.setattr(stats, "get_stats", lambda: _FAKE_STATS)
    code = cli.main(["stats", "--json"])
    assert code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == _FAKE_STATS


# ── transcribe ──────────────────────────────────────────────────────────────────

def test_transcribe_imprime_texte(capsys, monkeypatch, tmp_path):
    """transcribe imprime le texte simulé sur stdout (WAV temporaire réel)."""
    import core.transcriber as transcriber_mod
    monkeypatch.setattr(
        transcriber_mod.Transcriber, "transcribe",
        lambda self, audio: "texte simulé",
    )
    wav = _write_wav(tmp_path / "note.wav")
    code = cli.main(["transcribe", str(wav)])
    assert code == 0
    assert capsys.readouterr().out == "texte simulé\n"


def test_transcribe_fichier_absent(capsys):
    """Fichier introuvable → code non nul + message sur stderr, rien sur stdout."""
    code = cli.main(["transcribe", "/chemin/inexistant/introuvable.wav"])
    assert code != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "introuvable" in captured.err.lower()


def test_transcribe_translate_utilise_module(capsys, monkeypatch, tmp_path):
    """--translate-to appelle core.translator.translate et imprime sa sortie.

    core.translator est un module optionnel (« si présent »). On patche
    l'attribut du VRAI module pour ne dépendre ni du LLM ni du réseau.
    """
    import core.transcriber as transcriber_mod
    from core import translator as translator_mod

    monkeypatch.setattr(
        transcriber_mod.Transcriber, "transcribe",
        lambda self, audio: "texte source",
    )
    calls = []

    def fake_translate(text, target_lang, source_lang=None):
        calls.append((text, target_lang, source_lang))
        return f"[{target_lang}] {text}"

    monkeypatch.setattr(translator_mod, "translate", fake_translate)

    wav = _write_wav(tmp_path / "note.wav")
    code = cli.main(["transcribe", str(wav), "--translate-to", "en"])
    assert code == 0
    assert capsys.readouterr().out == "[en] texte source\n"
    # translate reçoit le texte transcrit, la langue cible et la langue source.
    assert calls == [("texte source", "en", "fr")]


def test_transcribe_translate_module_absent(capsys, monkeypatch, tmp_path):
    """--translate-to sans module de traduction → échec propre AVANT le modèle.

    On simule l'absence du module optionnel : on retire l'attribut `translator`
    du package core ET on force sys.modules[...] = None, ce qui fait lever
    ImportError à l'import. monkeypatch restaure tout automatiquement, donc ce
    test reste sans effet de bord sur les autres.
    """
    import core
    import core.transcriber as transcriber_mod
    monkeypatch.setattr(
        transcriber_mod.Transcriber, "transcribe",
        lambda self, audio: "NE DOIT PAS APPARAITRE",
    )
    monkeypatch.delattr(core, "translator", raising=False)
    monkeypatch.setitem(sys.modules, "core.translator", None)

    wav = _write_wav(tmp_path / "note.wav")
    code = cli.main(["transcribe", str(wav), "--translate-to", "en"])
    assert code != 0
    captured = capsys.readouterr()
    assert captured.out == ""  # rien imprimé : on échoue avant la transcription
    assert "core.translator" in captured.err


# ── WAV : lecture / conversion (mono 16 kHz) ─────────────────────────────────

def test_wav_reader_stereo_resample(tmp_path):
    """Stéréo 8 kHz → mono float32 16 kHz : longueur ~doublée, ndim 1."""
    seconds, sr = 0.1, 8000
    wav = _write_wav(tmp_path / "s.wav", samplerate=sr, channels=2, seconds=seconds)
    audio = cli._read_wav_as_float32_mono_16k(str(wav))
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    n_src = int(sr * seconds)
    # Rééchantillonnage 8 k → 16 k : environ 2x le nombre d'échantillons source.
    assert abs(len(audio) - 2 * n_src) <= 2
    assert np.all(np.abs(audio) <= 1.0)


def test_wav_reader_mono_16k_inchange(tmp_path):
    """Un WAV mono 16 kHz est lu sans rééchantillonnage (longueur préservée)."""
    seconds, sr = 0.1, 16000
    wav = _write_wav(tmp_path / "m.wav", samplerate=sr, channels=1, seconds=seconds)
    audio = cli._read_wav_as_float32_mono_16k(str(wav))
    assert len(audio) == int(sr * seconds)


# ── Aucune sous-commande ─────────────────────────────────────────────────────

def test_main_sans_commande_retourne_non_nul(capsys):
    code = cli.main([])
    assert code != 0
    # L'aide part sur stderr, stdout reste propre.
    assert capsys.readouterr().out == ""
