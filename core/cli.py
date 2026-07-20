"""
Interface en ligne de commande 100 % locale de Voxaho.

Différenciateur clé face à Wispr (qui n'a pas de CLI locale) : permet d'intégrer
la dictée/transcription Voxaho dans des workflows développeur — vibe coding,
scripts shell, pipelines CI — sans jamais envoyer d'audio dans le cloud.

Sous-commandes :
  transcribe   Transcrit un fichier WAV local et imprime le texte sur stdout.
  history      Liste l'historique local des dictées (texte lisible ou JSON).
  stats        Affiche les statistiques d'usage (texte lisible ou JSON).
  version      Affiche la version de Voxaho (core.__version__).

Conventions « script-friendly » :
  - `transcribe` et toute sortie `--json` écrivent UNIQUEMENT le résultat sur
    stdout ; les messages d'information, avertissements et erreurs vont sur
    stderr. Un pipe `voxaho transcribe x.wav | pbcopy` reçoit donc le texte pur.
  - Le code de retour est 0 en cas de succès, non nul en cas d'erreur (fichier
    introuvable, module indisponible, échec de transcription…).

Aucune dépendance ajoutée : uniquement la bibliothèque standard (argparse, wave,
json) plus numpy, déjà requis par le cœur de Voxaho. Les modules lourds
(faster-whisper via core.transcriber, numpy) sont importés paresseusement, dans
les seuls handlers qui en ont besoin : `import core.cli` reste léger et les
sous-commandes history/stats/version fonctionnent même si le moteur de
transcription n'est pas installé.
"""

import argparse
import json
import sys

# Fréquence d'échantillonnage attendue par Whisper (mono 16 kHz).
_TARGET_SR = 16000


# ── Lecture / conversion audio (stdlib wave + numpy) ─────────────────────────

def _resample_linear(data, orig_sr: int, target_sr: int):
    """Rééchantillonnage basique par interpolation linéaire (numpy).

    Simple et sans dépendance : suffisant pour préparer l'audio à Whisper. Pour
    une qualité optimale, fournir directement un WAV mono 16 kHz (aucune
    conversion n'est alors appliquée).
    """
    import numpy as np

    if orig_sr == target_sr or len(data) == 0:
        return data
    n_target = int(round(len(data) * target_sr / orig_sr))
    if n_target <= 0:
        return np.zeros(0, dtype=np.float32)
    # Positions normalisées [0, 1) : indépendantes du nombre d'échantillons.
    x_orig = np.linspace(0.0, 1.0, num=len(data), endpoint=False)
    x_target = np.linspace(0.0, 1.0, num=n_target, endpoint=False)
    return np.interp(x_target, x_orig, data).astype(np.float32)


def _read_wav_as_float32_mono_16k(path: str):
    """Lit un WAV PCM et renvoie un np.ndarray float32 mono 16 kHz dans [-1, 1].

    Gère mono/stéréo (mixage par moyenne des canaux), n'importe quelle fréquence
    (rééchantillonnage linéaire si ≠ 16 kHz) et les largeurs PCM 8/16/24/32 bits.
    Les WAV flottants (WAVE_FORMAT_IEEE_FLOAT) ne sont pas gérés par le module
    stdlib `wave` : fournir alors un WAV PCM classique.
    """
    import wave

    import numpy as np

    with wave.open(path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if not raw:
        return np.zeros(0, dtype=np.float32)

    # Décodage PCM → float32 normalisé dans [-1, 1] selon la largeur d'échantillon.
    if sampwidth == 1:
        # PCM 8 bits : NON signé (0..255), centré sur 128.
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0
    elif sampwidth == 2:
        # PCM 16 bits signé, little-endian.
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sampwidth == 3:
        # PCM 24 bits signé, little-endian : reconstruit à la main puis étend le signe.
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        ints = np.where(ints >= (1 << 23), ints - (1 << 24), ints)
        data = ints.astype(np.float32) / float(1 << 23)
    elif sampwidth == 4:
        # PCM 32 bits signé, little-endian.
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(
            f"largeur d'échantillon non supportée : {sampwidth} octet(s) "
            "(attendu 8, 16, 24 ou 32 bits PCM)"
        )

    # Mixage mono : moyenne des canaux si stéréo/multicanal.
    if n_channels > 1:
        data = data.reshape(-1, n_channels).mean(axis=1)

    # Rééchantillonnage vers 16 kHz si nécessaire.
    if framerate != _TARGET_SR:
        data = _resample_linear(data, framerate, _TARGET_SR)

    return np.ascontiguousarray(data, dtype=np.float32)


# ── Formatage lisible (mode texte) ───────────────────────────────────────────

def _format_history_text(entries: list) -> str:
    """Rend l'historique en texte lisible : un en-tête + le texte par dictée."""
    if not entries:
        return "Aucune dictée dans l'historique."
    lines = []
    for e in entries:
        meta = []
        if e.get("language"):
            meta.append(str(e["language"]))
        if e.get("model"):
            meta.append(str(e["model"]))
        if e.get("word_count") is not None:
            meta.append(f"{e['word_count']} mots")
        meta_str = f" ({', '.join(meta)})" if meta else ""
        fav = " ★" if e.get("favorite") else ""
        lines.append(f"[{e.get('id')}] {e.get('created_at')}{meta_str}{fav}")
        lines.append(f"    {(e.get('text') or '').strip()}")
    return "\n".join(lines)


def _format_stats_text(s: dict) -> str:
    """Rend les statistiques d'usage en texte lisible aligné."""
    return "\n".join([
        "Statistiques Voxaho",
        f"  Dictées totales      : {s.get('total_dictations', 0)}",
        f"  Mots totaux          : {s.get('total_words', 0)}",
        f"  Caractères totaux    : {s.get('total_chars', 0)}",
        f"  Moyenne mots/dictée  : {s.get('avg_words_per_dictation', 0)}",
        f"  Dictées aujourd'hui  : {s.get('today_dictations', 0)}",
        f"  Mots aujourd'hui     : {s.get('today_words', 0)}",
        f"  Première utilisation : {s.get('first_use_date') or '—'}",
        f"  Temps gagné (min)    : {s.get('time_saved_minutes', 0)}",
    ])


def _print_json(obj) -> None:
    """Imprime `obj` en JSON (UTF-8 lisible) sur stdout, avec saut de ligne final."""
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


# ── Handlers des sous-commandes ──────────────────────────────────────────────

def _cmd_version(args) -> int:
    """Affiche core.__version__ sur stdout."""
    from core import __version__
    print(__version__)
    return 0


def _cmd_history(args) -> int:
    """Liste l'historique local (texte lisible par défaut, JSON avec --json)."""
    try:
        from core import history
    except ImportError as e:
        print(f"Erreur : module historique indisponible ({e}).", file=sys.stderr)
        return 1

    try:
        entries = history.list_entries(
            limit=args.limit,
            search=args.search,
            favorites_only=args.favorites,
        )
    except Exception as e:
        print(f"Erreur lors de la lecture de l'historique : {e}", file=sys.stderr)
        return 1

    if args.json:
        _print_json(entries)
    else:
        print(_format_history_text(entries))
    return 0


def _cmd_stats(args) -> int:
    """Affiche les statistiques d'usage (texte lisible ou JSON avec --json)."""
    try:
        from core import stats
    except ImportError as e:
        print(f"Erreur : module statistiques indisponible ({e}).", file=sys.stderr)
        return 1

    try:
        data = stats.get_stats()
    except Exception as e:
        print(f"Erreur lors du calcul des statistiques : {e}", file=sys.stderr)
        return 1

    if args.json:
        _print_json(data)
    else:
        print(_format_stats_text(data))
    return 0


def _cmd_transcribe(args) -> int:
    """Transcrit un WAV local et imprime le texte transcrit sur stdout.

    Ordre : validation du fichier → (si --translate-to) vérification précoce du
    module de traduction → lecture/conversion du WAV → transcription → traduction
    éventuelle → impression du résultat. On échoue AVANT de charger le modèle si
    la traduction est demandée mais indisponible (fast-fail pratique en script).
    """
    import os

    path = args.file
    if not os.path.isfile(path):
        print(f"Erreur : fichier introuvable : {path}", file=sys.stderr)
        return 1

    # Vérification précoce du module de traduction (optionnel) : évite de charger
    # tout le modèle Whisper pour échouer ensuite. core.translator peut ne pas
    # exister (fonctionnalité optionnelle) → message clair et code non nul.
    translator = None
    if args.translate_to:
        try:
            from core import translator as translator_mod
            translator = translator_mod
        except ImportError:
            print(
                "Erreur : --translate-to demandé mais le module de traduction "
                "(core.translator) est indisponible dans cette installation.",
                file=sys.stderr,
            )
            return 1

    # Lecture + conversion de l'audio (float32 mono 16 kHz).
    try:
        audio = _read_wav_as_float32_mono_16k(path)
    except Exception as e:
        print(f"Erreur lors de la lecture du WAV : {e}", file=sys.stderr)
        return 1

    # Import paresseux du moteur de transcription (dépendance lourde).
    try:
        from core.transcriber import Transcriber
    except ImportError as e:
        print(f"Erreur : moteur de transcription indisponible ({e}).", file=sys.stderr)
        return 1

    try:
        transcriber = Transcriber(
            model=args.model,
            language=args.language,
            reformatting=not args.no_reformat,
            beam_size=1,
        )
        text = transcriber.transcribe(audio)
    except Exception as e:
        print(f"Erreur pendant la transcription : {e}", file=sys.stderr)
        return 1

    # Traduction optionnelle (uniquement si module dispo, vérifié plus haut).
    if translator is not None:
        source = None if args.language == "auto" else args.language
        try:
            text = translator.translate(text, args.translate_to, source_lang=source)
        except Exception as e:
            print(f"Erreur pendant la traduction : {e}", file=sys.stderr)
            return 1

    # Résultat pur sur stdout (destiné aux scripts/pipes).
    print(text)
    return 0


# ── Construction du parseur ──────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    """Construit le parseur argparse et ses sous-commandes."""
    parser = argparse.ArgumentParser(
        prog="voxaho",
        description="Voxaho — dictée vocale 100 % locale, en ligne de commande.",
        epilog=(
            "Exemples :\n"
            "  voxaho transcribe note.wav\n"
            "  voxaho transcribe note.wav --model medium --language en\n"
            "  voxaho transcribe note.wav --no-reformat > note.txt\n"
            "  voxaho transcribe note.wav --translate-to en\n"
            "  voxaho history --limit 5 --search facture\n"
            "  voxaho history --favorites --json | jq .\n"
            "  voxaho stats --json\n"
            "  voxaho version"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<commande>")

    # transcribe
    p_tr = sub.add_parser(
        "transcribe",
        help="Transcrit un fichier WAV local et imprime le texte sur stdout.",
        description="Transcrit un fichier WAV local (mono/stéréo, tout samplerate).",
    )
    p_tr.add_argument("file", help="Chemin du fichier WAV à transcrire.")
    p_tr.add_argument(
        "--model", default="small",
        help="Modèle Whisper (défaut : small).",
    )
    p_tr.add_argument(
        "--language", default="fr",
        help="Langue de l'audio, ou 'auto' pour détection (défaut : fr).",
    )
    p_tr.add_argument(
        "--no-reformat", action="store_true",
        help="Désactive le reformatage (ponctuation/majuscules) : texte brut.",
    )
    p_tr.add_argument(
        "--translate-to", metavar="LANG", default=None,
        help="Traduit le texte transcrit vers LANG (si core.translator présent).",
    )
    p_tr.set_defaults(func=_cmd_transcribe)

    # history
    p_hist = sub.add_parser(
        "history",
        help="Liste l'historique local des dictées.",
        description="Liste l'historique local des dictées (texte ou JSON).",
    )
    p_hist.add_argument(
        "--limit", type=int, default=20,
        help="Nombre maximum d'entrées à afficher (défaut : 20).",
    )
    p_hist.add_argument(
        "--search", default=None,
        help="Filtre les dictées contenant ce terme (insensible à la casse).",
    )
    p_hist.add_argument(
        "--favorites", action="store_true",
        help="N'affiche que les dictées marquées comme favorites.",
    )
    p_hist.add_argument(
        "--json", action="store_true",
        help="Sortie JSON (pour scripts) au lieu du texte lisible.",
    )
    p_hist.set_defaults(func=_cmd_history)

    # stats
    p_stats = sub.add_parser(
        "stats",
        help="Affiche les statistiques d'usage.",
        description="Affiche les statistiques d'usage agrégées (texte ou JSON).",
    )
    p_stats.add_argument(
        "--json", action="store_true",
        help="Sortie JSON (pour scripts) au lieu du texte lisible.",
    )
    p_stats.set_defaults(func=_cmd_stats)

    # version
    p_ver = sub.add_parser(
        "version",
        help="Affiche la version de Voxaho.",
        description="Affiche la version de Voxaho (core.__version__).",
    )
    p_ver.set_defaults(func=_cmd_version)

    return parser


# ── Point d'entrée ───────────────────────────────────────────────────────────

def main(argv=None) -> int:
    """Point d'entrée de la CLI. Retourne un code de sortie (0 = succès).

    argv : liste d'arguments (hors nom de programme) ; None → sys.argv[1:].
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Aucune sous-commande : affiche l'aide sur stderr et retourne un code non nul
    # (stdout reste propre pour les usages en pipe).
    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 1

    return args.func(args)
