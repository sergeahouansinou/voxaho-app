"""
Tests Phase 3b — détection matérielle + recommandation de modèle (core.hardware).

Aucune dépendance externe, aucune QApplication : on teste que detect_hardware()
ne lève jamais et renvoie les clés/types attendus, et que recommend_model() —
fonction PURE — couvre tous les paliers avec des modèles valides et une raison
non vide, y compris sur des dicts vides ou partiels.
Lancer : ./venv/bin/python -m pytest tests/test_hardware.py -v
"""

import sys
from pathlib import Path

# Racine du projet importable quel que soit le mode d'invocation de pytest.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import hardware


# ── detect_hardware() ─────────────────────────────────────────────────────────

def test_detect_ne_leve_pas():
    """detect_hardware() ne lève jamais (100 % défensif)."""
    hw = hardware.detect_hardware()
    assert isinstance(hw, dict)


def test_detect_cles_presentes():
    """Toutes les clés du contrat sont présentes."""
    hw = hardware.detect_hardware()
    for key in ("platform", "is_arm64", "cpu_count", "ram_gb",
                "has_metal", "mlx_available"):
        assert key in hw, f"clé manquante : {key!r}"


def test_detect_types_corrects():
    """Chaque champ a le type attendu."""
    hw = hardware.detect_hardware()
    assert isinstance(hw["platform"], str)
    assert isinstance(hw["is_arm64"], bool)
    assert isinstance(hw["cpu_count"], int)
    assert isinstance(hw["ram_gb"], float)
    assert isinstance(hw["has_metal"], bool)
    assert isinstance(hw["mlx_available"], bool)


def test_detect_valeurs_plausibles():
    """cpu_count >= 1 et ram_gb > 0 (jamais 0 ou négatif)."""
    hw = hardware.detect_hardware()
    assert hw["cpu_count"] >= 1
    assert hw["ram_gb"] > 0


def test_detect_metal_implique_arm64():
    """has_metal n'est vrai que sur macOS + arm64."""
    hw = hardware.detect_hardware()
    if hw["has_metal"]:
        assert hw["platform"] == "darwin"
        assert hw["is_arm64"] is True


# ── recommend_model() : structure ─────────────────────────────────────────────

def _assert_reco_valide(reco):
    """Vérifie qu'une reco respecte le contrat (modèle valide, raison, etc.)."""
    assert isinstance(reco, dict)
    assert reco["model"] in hardware.VALID_MODELS
    assert reco["compute_backend"] in ("auto", "cpu", "mlx")
    assert isinstance(reco["beam_size"], int) and reco["beam_size"] >= 1
    assert isinstance(reco["reason"], str) and reco["reason"].strip()


def test_reco_sur_detection_reelle():
    """La reco issue de la détection réelle respecte le contrat."""
    _assert_reco_valide(hardware.recommend_model(hardware.detect_hardware()))


# ── recommend_model() : tous les paliers ──────────────────────────────────────

def test_reco_palier_16_arm():
    """16 Go + Apple Silicon → large-v3-turbo / auto."""
    reco = hardware.recommend_model(
        {"ram_gb": 16.0, "cpu_count": 10, "is_arm64": True}
    )
    _assert_reco_valide(reco)
    assert reco["model"] == "large-v3-turbo"
    assert reco["compute_backend"] == "auto"


def test_reco_palier_16_non_arm():
    """16 Go sans Apple Silicon → medium."""
    reco = hardware.recommend_model(
        {"ram_gb": 32.0, "cpu_count": 8, "is_arm64": False}
    )
    _assert_reco_valide(reco)
    assert reco["model"] == "medium"


def test_reco_palier_8():
    """Entre 8 et 16 Go → small."""
    reco = hardware.recommend_model(
        {"ram_gb": 12.0, "cpu_count": 6, "is_arm64": False}
    )
    _assert_reco_valide(reco)
    assert reco["model"] == "small"


def test_reco_palier_ram_faible():
    """Moins de 8 Go → tiny."""
    reco = hardware.recommend_model(
        {"ram_gb": 4.0, "cpu_count": 8, "is_arm64": False}
    )
    _assert_reco_valide(reco)
    assert reco["model"] == "tiny"


def test_reco_palier_cpu_faible():
    """CPU <= 2 cœurs → tiny même avec beaucoup de RAM."""
    reco = hardware.recommend_model(
        {"ram_gb": 32.0, "cpu_count": 2, "is_arm64": True}
    )
    _assert_reco_valide(reco)
    assert reco["model"] == "tiny"


# ── recommend_model() : robustesse (dict vide/partiel) ────────────────────────

def test_reco_dict_vide():
    """Un dict vide donne une reco valide (défaut prudent)."""
    _assert_reco_valide(hardware.recommend_model({}))


def test_reco_dict_partiel():
    """Un dict partiel (RAM seule) donne une reco valide."""
    _assert_reco_valide(hardware.recommend_model({"ram_gb": 16.0}))


def test_reco_non_dict():
    """Une entrée non-dict (None) ne casse pas la reco."""
    _assert_reco_valide(hardware.recommend_model(None))


def test_reco_valeurs_aberrantes():
    """Des types aberrants (str) sont absorbés sans lever."""
    reco = hardware.recommend_model(
        {"ram_gb": "beaucoup", "cpu_count": "plein", "is_arm64": True}
    )
    _assert_reco_valide(reco)


# ── specs_summary() (fonction pure d'affichage) ───────────────────────────────

def test_specs_summary_contenu():
    """Le résumé mentionne cœurs, RAM et le type de puce."""
    summary = hardware.specs_summary(
        {"cpu_count": 10, "ram_gb": 16.0, "is_arm64": True}
    )
    assert isinstance(summary, str) and summary.strip()
    assert "10" in summary
    assert "16" in summary
    assert "Apple Silicon" in summary


def test_specs_summary_non_arm():
    """Sans Apple Silicon, le résumé indique CPU x86."""
    summary = hardware.specs_summary(
        {"cpu_count": 4, "ram_gb": 8.0, "is_arm64": False}
    )
    assert "CPU x86" in summary


def test_specs_summary_robuste():
    """specs_summary() tolère un dict vide/non-dict sans lever."""
    assert isinstance(hardware.specs_summary({}), str)
    assert isinstance(hardware.specs_summary(None), str)
