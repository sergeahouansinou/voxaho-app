"""
Reformatage IA local (Phase 2B) — wrapper 100 % LOCAL autour d'un petit LLM.

Objectif : mettre en forme intelligemment le texte dicté (bien au-delà du
nettoyage par règles) — ponctuation, casse, suppression des hésitations et
répétitions, phrases claires — SANS jamais changer le sens ni ajouter
d'information.

Modèle : Qwen2.5-1.5B-Instruct quantifié GGUF Q4, exécuté via
llama-cpp-python (Metal sur Apple Silicon, CPU sinon). Le GGUF embarque son
propre chat template, exploité par create_chat_completion().

DÉPENDANCE OPTIONNELLE — principe directeur de tout ce module :
  l'application DOIT fonctionner à 100 % même si llama_cpp est absent OU si le
  modèle n'est pas téléchargé. Toute l'API publique dégrade gracieusement :
    - is_available() / is_model_ready() → booléens, ne lèvent jamais ;
    - download_model() → True/False ;
    - reformat() → None dès que l'IA est indisponible ou en cas d'erreur, ce qui
      indique au caller de retomber sur son nettoyage par règles ;
    - unload() → silencieux.
  Aucune fonction publique ne propage d'exception.

Points d'injection isolant l'accès au natif (mockables sans installer
llama_cpp) : _import_llama() (import protégé du module) et _get_llm() (cache
d'instance thread-safe).
"""

import os
import logging
import threading

logger = logging.getLogger(__name__)

# ── Constantes modèle (API publique — d'autres agents en dépendent) ────────────
MODEL_REPO     = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
MODEL_FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
MODELS_DIR     = os.path.expanduser("~/.voxaho/models")
MODEL_PATH     = os.path.join(MODELS_DIR, MODEL_FILENAME)

# Cache module-level de l'instance Llama, protégé par un lock (le chargement
# est coûteux et doit être thread-safe : plusieurs dictées peuvent arriver en
# parallèle). Reste None tant que le modèle n'a pas été chargé (lazy).
_llm = None
_llm_lock = threading.Lock()


class LLMError(Exception):
    """Erreur interne du wrapper LLM. L'API publique ne la propage JAMAIS."""


# ── System prompts par langue ──────────────────────────────────────────────────
# Consigne stricte : mettre en forme UNIQUEMENT (jamais réécrire le fond),
# et répondre exclusivement par le texte corrigé (aucun préambule, aucun
# guillemet, aucune explication). Langue inconnue → français par défaut.
_SYSTEM_PROMPTS = {
    "fr": (
        "Tu es un correcteur. Reformate ce texte dicté à la voix : corrige la "
        "ponctuation, les majuscules, enlève les hésitations et répétitions, "
        "rends-le clair et naturel. Ne change PAS le sens, n'ajoute AUCUNE "
        "information, ne réponds QU'avec le texte corrigé."
    ),
    "en": (
        "You are a proofreader. Reformat this voice-dictated text: fix "
        "punctuation and capitalization, remove hesitations and repetitions, "
        "make it clear and natural. Do NOT change the meaning, do NOT add ANY "
        "information, reply ONLY with the corrected text."
    ),
    "es": (
        "Eres un corrector. Reformatea este texto dictado por voz: corrige la "
        "puntuación y las mayúsculas, elimina las vacilaciones y repeticiones, "
        "hazlo claro y natural. NO cambies el sentido, NO añadas NINGUNA "
        "información, responde SOLO con el texto corregido."
    ),
    "de": (
        "Du bist ein Korrektor. Formatiere diesen per Sprache diktierten Text: "
        "korrigiere Zeichensetzung und Großschreibung, entferne Zögern und "
        "Wiederholungen, mache ihn klar und natürlich. Ändere NICHT den Sinn, "
        "füge KEINE Information hinzu, antworte NUR mit dem korrigierten Text."
    ),
    "it": (
        "Sei un correttore. Riformatta questo testo dettato a voce: correggi la "
        "punteggiatura e le maiuscole, elimina le esitazioni e le ripetizioni, "
        "rendilo chiaro e naturale. NON cambiare il senso, NON aggiungere "
        "ALCUNA informazione, rispondi SOLO con il testo corretto."
    ),
}

# Préfixes parasites que le modèle ajoute parfois malgré la consigne
# (« Voici le texte corrigé : … »). Comparés en minuscules ; ordre du plus
# long au plus court pour retirer le préfixe complet quand il y en a un.
_JUNK_PREFIXES = (
    "voici le texte corrigé",
    "voici le texte reformaté",
    "voici le texte mis en forme",
    "voici le texte",
    "texte corrigé",
    "here is the corrected text",
    "here's the corrected text",
    "corrected text",
    "aquí está el texto corregido",
    "hier ist der korrigierte text",
    "ecco il testo corretto",
)

# Facteur de dérive maximal toléré : une sortie plus de N fois plus longue que
# l'entrée trahit une hallucination du LLM → on préfère le fallback règles.
_MAX_GROWTH_FACTOR = 3


# ── Accès au natif (points d'injection isolés, mockables) ──────────────────────

def _import_llama():
    """Importe et retourne la classe `Llama` de llama_cpp, ou None si indisponible.

    Import protégé : ne lève jamais (ImportError si le paquet est absent, mais
    aussi toute erreur de chargement de la bibliothèque native). Unique point
    d'accès au module — monkeypatchable dans les tests pour simuler la présence
    ou l'absence de llama_cpp sans avoir à l'installer réellement.
    """
    try:
        from llama_cpp import Llama
        return Llama
    except Exception as e:  # ImportError + erreurs de chargement du natif
        logger.debug(f"llama_cpp indisponible : {e}")
        return None


def _get_llm():
    """Charge (lazy) et met en cache l'instance Llama. Thread-safe. Ne lève jamais.

    Retourne l'instance prête, ou None si llama_cpp est indisponible, si le
    modèle GGUF est absent, ou si le chargement échoue. n_gpu_layers=-1 place
    toutes les couches sur GPU/Metal si disponible ; llama.cpp l'ignore
    proprement en CPU.
    """
    global _llm
    if _llm is not None:
        return _llm
    with _llm_lock:
        if _llm is not None:  # double-check après acquisition du lock
            return _llm

        Llama = _import_llama()
        if Llama is None:
            return None
        if not is_model_ready():
            logger.warning("Modèle GGUF absent — reformatage IA indisponible")
            return None

        try:
            _llm = Llama(
                model_path=MODEL_PATH,
                n_ctx=2048,
                n_gpu_layers=-1,   # tout sur GPU/Metal si dispo, sinon CPU
                verbose=False,
            )
        except Exception as e:
            logger.warning(f"Chargement du modèle Llama échoué : {e}")
            _llm = None
            return None
        return _llm


# ── API publique ───────────────────────────────────────────────────────────────

def is_available() -> bool:
    """True si llama_cpp est importable (le reformatage IA est donc possible)."""
    return _import_llama() is not None


def is_model_ready() -> bool:
    """True si le fichier GGUF du modèle existe sur disque (taille > 0)."""
    try:
        return os.path.isfile(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 0
    except OSError:
        return False


def download_model(progress_cb=None) -> bool:
    """Télécharge le GGUF du modèle depuis Hugging Face Hub. Retourne True/False.

    Utilise huggingface_hub.hf_hub_download (huggingface_hub est déjà présent :
    c'est une dépendance de faster-whisper), en écrivant le fichier réel dans
    MODELS_DIR — d'où un chemin final égal à MODEL_PATH.

    progress_cb(fraction: float) est optionnel. hf_hub_download ne fournit pas de
    progression fine : le callback n'est donc appelé qu'au début (0.0) et à la
    fin (1.0). Tolérant : toute erreur (réseau, disque, dépendance absente) est
    loguée et renvoie False, sans jamais lever.
    """
    try:
        from huggingface_hub import hf_hub_download
    except Exception as e:
        logger.warning(f"huggingface_hub indisponible : {e}")
        return False

    def _emit(fraction: float) -> None:
        # Le callback ne doit jamais faire échouer le téléchargement.
        if progress_cb is None:
            return
        try:
            progress_cb(fraction)
        except Exception as e:
            logger.debug(f"progress_cb a levé (ignoré) : {e}")

    try:
        os.makedirs(MODELS_DIR, exist_ok=True)
        _emit(0.0)
        hf_hub_download(
            repo_id=MODEL_REPO,
            filename=MODEL_FILENAME,
            local_dir=MODELS_DIR,
        )
        _emit(1.0)
        return is_model_ready()
    except Exception as e:
        logger.warning(f"Téléchargement du modèle échoué : {e}")
        return False


def reformat(text: str, language: str = "fr") -> str | None:
    """Reformate le texte dicté via le LLM local. Ne lève JAMAIS.

    Retourne :
      - "" si le texte d'entrée est vide/blanc (indépendamment de l'IA) ;
      - le texte mis en forme si le modèle a produit une sortie plausible ;
      - None si llama_cpp est indisponible, si le modèle est absent, en cas
        d'erreur, ou si le garde-fou anti-hallucination se déclenche. Dans tous
        les cas None, le caller doit retomber sur son nettoyage par règles.
    """
    if not text or not text.strip():
        return ""

    llm = _get_llm()
    if llm is None:
        return None

    system_prompt = _SYSTEM_PROMPTS.get(language, _SYSTEM_PROMPTS["fr"])

    # Budget de sortie adapté à l'entrée : ~1 token pour 2 caractères plus une
    # marge fixe pour la ponctuation ajoutée, borné [64, 1024] (n_ctx = 2048).
    max_tokens = min(1024, max(64, len(text) // 2 + 64))

    try:
        resp = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": text},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        raw = resp["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning(f"Reformatage IA échoué : {e}")
        return None

    cleaned = _postprocess(raw)

    # Garde-fou anti-hallucination : une sortie vide ou disproportionnée par
    # rapport à l'entrée trahit une dérive du modèle → fallback règles.
    if not cleaned:
        return None
    if len(cleaned) > len(text) * _MAX_GROWTH_FACTOR:
        logger.warning("Sortie IA anormalement longue — fallback règles")
        return None
    return cleaned


def unload() -> None:
    """Libère l'instance Llama en cache (fermeture propre). Silencieux."""
    global _llm
    with _llm_lock:
        _llm = None


# ── Post-traitement de la sortie du LLM ────────────────────────────────────────

def _strip_enclosing_quotes(s: str) -> str:
    """Retire une paire de guillemets englobants (droits, simples ou typographiques)."""
    pairs = {'"': '"', "'": "'", "«": "»", "“": "”", "‘": "’"}
    s = s.strip()
    if len(s) >= 2 and s[0] in pairs and s[-1] == pairs[s[0]]:
        return s[1:-1].strip()
    return s


def _postprocess(text: str) -> str:
    """Nettoie la réponse brute du modèle : strip, préfixes parasites, guillemets.

    Robuste à l'ordre : on retire d'abord un éventuel préfixe (« Voici le
    texte : … »), puis d'éventuels guillemets englobants (qui peuvent entourer
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
