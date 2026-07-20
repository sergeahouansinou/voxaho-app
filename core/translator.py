"""
Traduction à la volée 100 % LOCALE (Phase 3 « Au-delà de Wispr »).

Objectif : dicter dans une langue et OBTENIR le texte dans une AUTRE langue
(ex. dicter en français → écrire en anglais). La traduction s'insère APRÈS le
reformatage du transcriber : on traduit donc un texte DÉJÀ propre.

Réutilise le même petit LLM local que le reformatage (Qwen2.5-1.5B-Instruct
GGUF Q4, via llama-cpp-python), mais avec un PROMPT DE TRADUCTION dédié — jamais
`core.llm.reformat` (dont le prompt est un correcteur, pas un traducteur).

── Mécanisme d'accès au LLM (choix documenté) ──────────────────────────────────
core/llm.py n'expose pas d'API de chat générique et n'appartient PAS à ce module
(interdiction de le modifier). On accède donc au modèle en deux temps, du plus
économe au plus autonome :
  1. RÉUTILISATION de l'instance partagée : si `core.llm._get_llm` est accessible
     (getattr), on récupère l'instance Llama déjà chargée par le reformatage et on
     l'appelle en bas niveau (`create_chat_completion`) avec NOTRE prompt. Un seul
     modèle réside alors en mémoire pour toute l'application.
  2. FALLBACK autonome : si l'instance partagée est indisponible, on charge (lazy,
     thread-safe, cache module-level) NOTRE PROPRE instance Llama depuis
     `core.llm.MODEL_PATH`, via un import défensif de `llama_cpp` local à ce module.

DÉPENDANCE OPTIONNELLE — principe directeur : l'application DOIT fonctionner à
100 % même sans llama_cpp et sans modèle. Toute l'API publique dégrade
gracieusement et ne propage JAMAIS d'exception :
  - is_available() → bool ;
  - translate() → "" (entrée vide), None (cible inconnue / LLM indispo / erreur /
    garde-fou), ou le texte traduit nettoyé. None indique au caller de GARDER le
    texte original ;
  - unload() → silencieux, ne libère QUE l'instance locale éventuellement créée
    ici (jamais l'instance partagée de core.llm).
"""

import os
import logging
import threading

logger = logging.getLogger(__name__)

# ── Codes langue → nom anglais (pour le prompt de traduction) ───────────────────
# Couvre l'intégralité des cibles proposées par l'UI (ui/settings_window.LANGS,
# hors « auto ») afin que translate() ne retombe jamais sur None pour une langue
# légitimement choisie par l'utilisateur. Le nom anglais est celui compris le plus
# fiablement par le LLM dans la consigne système.
LANGUAGE_NAMES = {
    "fr": "French",
    "en": "English",
    "es": "Spanish",
    "de": "German",
    "it": "Italian",
    "id": "Indonesian",
    "ms": "Malay",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "hr": "Croatian",
    "hu": "Hungarian",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "fi": "Finnish",
    "sv": "Swedish",
    "vi": "Vietnamese",
    "tr": "Turkish",
    "el": "Greek",
    "bg": "Bulgarian",
    "ru": "Russian",
    "sr": "Serbian",
    "uk": "Ukrainian",
    "he": "Hebrew",
    "ar": "Arabic",
    "fa": "Persian",
    "hi": "Hindi",
    "ta": "Tamil",
    "th": "Thai",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}

# Facteur de dérive maximal toléré : une traduction plus de N fois plus longue que
# l'entrée trahit une hallucination du LLM → on préfère garder l'original.
# Identique au garde-fou de core.llm.reformat.
_MAX_GROWTH_FACTOR = 3

# Préfixes méta que le modèle ajoute parfois malgré la consigne (« Sure, here is
# the translation: … »). Comparés en minuscules, du plus long au plus court.
_JUNK_PREFIXES = (
    "sure, here is the translation",
    "sure, here's the translation",
    "here is the translation",
    "here's the translation",
    "the translation is",
    "translation",
    "translated text",
    "voici la traduction",
    "traduction",
)

# Cache de NOTRE PROPRE instance Llama (chemin fallback uniquement), protégé par
# un lock : reste None tant qu'on n'a pas dû charger nous-mêmes le modèle.
_own_llm = None
_own_llm_lock = threading.Lock()


# ── Accès au natif (points d'injection isolés, mockables) ──────────────────────

def _import_llama():
    """Importe et retourne la classe `Llama` de llama_cpp, ou None si indisponible.

    Import protégé (ne lève jamais) : mockable dans les tests pour simuler la
    présence/absence de llama_cpp sans l'installer. N'est utilisé que par le
    chemin FALLBACK (chargement de notre propre instance).
    """
    try:
        from llama_cpp import Llama
        return Llama
    except Exception as e:  # ImportError + erreurs de chargement du natif
        logger.debug(f"llama_cpp indisponible : {e}")
        return None


def _model_path():
    """Chemin du GGUF, lu défensivement depuis core.llm.MODEL_PATH (ou None)."""
    try:
        from core import llm
        return getattr(llm, "MODEL_PATH", None)
    except Exception as e:  # pragma: no cover - purement défensif
        logger.debug(f"core.llm.MODEL_PATH inaccessible : {e}")
        return None


def _load_own_llm():
    """Charge (lazy) et met en cache NOTRE propre instance Llama. Thread-safe.

    Ne lève jamais. Retourne l'instance prête, ou None si llama_cpp est absent, si
    le GGUF est introuvable/vide, ou si le chargement échoue. n_ctx et n_gpu_layers
    calqués sur core.llm pour un comportement identique (GPU/Metal si dispo).
    """
    global _own_llm
    if _own_llm is not None:
        return _own_llm
    with _own_llm_lock:
        if _own_llm is not None:  # double-check après acquisition du lock
            return _own_llm

        Llama = _import_llama()
        if Llama is None:
            return None

        model_path = _model_path()
        try:
            ready = bool(model_path) and os.path.isfile(model_path) and os.path.getsize(model_path) > 0
        except OSError:
            ready = False
        if not ready:
            logger.warning("Modèle GGUF absent — traduction indisponible")
            return None

        try:
            _own_llm = Llama(
                model_path=model_path,
                n_ctx=2048,
                n_gpu_layers=-1,   # tout sur GPU/Metal si dispo, sinon CPU
                verbose=False,
            )
        except Exception as e:
            logger.warning(f"Chargement du modèle Llama (traduction) échoué : {e}")
            _own_llm = None
            return None
        return _own_llm


def _get_llm():
    """Retourne une instance Llama prête à traduire, ou None. Ne lève jamais.

    Stratégie documentée en tête de module : réutiliser l'instance partagée de
    core.llm quand elle est accessible (un seul modèle en mémoire), sinon charger
    NOTRE propre instance.
    """
    # 1. Réutilisation de l'instance partagée de core.llm.
    try:
        from core import llm as _llm_mod
    except Exception:  # pragma: no cover - purement défensif
        _llm_mod = None
    if _llm_mod is not None:
        shared_getter = getattr(_llm_mod, "_get_llm", None)
        if callable(shared_getter):
            try:
                inst = shared_getter()
            except Exception as e:  # pragma: no cover - purement défensif
                logger.debug(f"Instance LLM partagée indisponible : {e}")
                inst = None
            if inst is not None:
                return inst

    # 2. Fallback : notre propre instance.
    return _load_own_llm()


# ── API publique ───────────────────────────────────────────────────────────────

def is_available() -> bool:
    """True si le LLM sous-jacent est disponible ET son modèle prêt.

    Import défensif de core.llm : module absent ou toute erreur → False (la
    traduction est alors traitée comme indisponible, le caller garde l'original).
    """
    try:
        from core import llm
        return bool(llm.is_available()) and bool(llm.is_model_ready())
    except Exception as e:  # pragma: no cover - purement défensif
        logger.debug(f"core.llm indisponible : {e}")
        return False


def translate(text: str, target_lang: str, source_lang: str | None = None) -> str | None:
    """Traduit `text` vers `target_lang` via le LLM local. Ne lève JAMAIS.

    Retourne :
      - "" si le texte d'entrée est vide/blanc (indépendamment du LLM) ;
      - None si `target_lang` est absent/inconnu, si le LLM est indisponible, en
        cas d'erreur, ou si un garde-fou anti-hallucination se déclenche. Dans tous
        les cas None, le caller DOIT garder le texte original ;
      - sinon le texte traduit, nettoyé (préfixes/guillemets parasites retirés).

    `source_lang` (optionnel) précise la langue source dans la consigne quand elle
    est connue ; ignorée si inconnue.
    """
    if not text or not text.strip():
        return ""
    if not target_lang or target_lang not in LANGUAGE_NAMES:
        return None

    llm_inst = _get_llm()
    if llm_inst is None:
        return None

    target_name = LANGUAGE_NAMES[target_lang]
    source_name = LANGUAGE_NAMES.get(source_lang) if source_lang else None

    # Consigne système dédiée TRADUCTION (jamais le prompt correcteur de reformat).
    if source_name and source_name != target_name:
        system_prompt = (
            "You are a professional translator. Translate the user's text from "
            f"{source_name} into {target_name}. Output ONLY the translation, no "
            "comments, no quotes, preserve meaning and tone."
        )
    else:
        system_prompt = (
            "You are a professional translator. Translate the user's text into "
            f"{target_name}. Output ONLY the translation, no comments, no quotes, "
            "preserve meaning and tone."
        )

    # Budget de sortie : une traduction peut être plus longue que la source ; on
    # laisse ~1 token/caractère + marge, borné [128, 1024] (n_ctx = 2048).
    max_tokens = min(1024, max(128, len(text) + 128))

    try:
        resp = llm_inst.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": text},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        raw = resp["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning(f"Traduction échouée : {e}")
        return None

    cleaned = _postprocess(raw)

    # Garde-fous anti-hallucination (identiques à core.llm.reformat) : sortie vide
    # ou disproportionnée → on renonce (le caller garde l'original).
    if not cleaned:
        return None
    if len(cleaned) > len(text) * _MAX_GROWTH_FACTOR:
        logger.warning("Traduction anormalement longue — original conservé")
        return None
    return cleaned


def unload() -> None:
    """Libère NOTRE instance Llama locale si on en a créé une. Silencieux.

    Ne touche JAMAIS à l'instance partagée de core.llm (elle appartient au module
    de reformatage, qui gère son propre cycle de vie via core.llm.unload()).
    """
    global _own_llm
    with _own_llm_lock:
        _own_llm = None


# ── Post-traitement de la sortie du LLM ────────────────────────────────────────

def _strip_enclosing_quotes(s: str) -> str:
    """Retire une paire de guillemets englobants (droits, simples ou typographiques)."""
    pairs = {'"': '"', "'": "'", "«": "»", "“": "”", "‘": "’"}
    s = s.strip()
    if len(s) >= 2 and s[0] in pairs and s[-1] == pairs[s[0]]:
        return s[1:-1].strip()
    return s


def _postprocess(text: str) -> str:
    """Nettoie la réponse brute du modèle : strip, préfixes méta, guillemets.

    Robuste à l'ordre : on retire d'abord un éventuel préfixe (« Here is the
    translation: … »), puis d'éventuels guillemets englobants (qui peuvent entourer
    le contenu situé après le préfixe).
    """
    out = (text or "").strip()

    low = out.lower()
    for prefix in _JUNK_PREFIXES:
        if low.startswith(prefix):
            rest = out[len(prefix):].lstrip()
            # Retire le séparateur qui suit parfois le préfixe (« : », « - »…).
            rest = rest.lstrip(":-–—").lstrip()
            out = rest
            break

    out = _strip_enclosing_quotes(out)
    return out.strip()
