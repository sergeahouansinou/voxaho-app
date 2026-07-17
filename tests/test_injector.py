"""
Tests du correctif C2 — sauvegarde/restauration du presse-papiers autour
de l'injection (core/injector.py).

Exécutables sur macOS sans aucune interaction réelle : toutes les fonctions
presse-papiers / collage / sleep sont monkeypatchées et enregistrent leurs
appels dans une liste, ce qui permet de vérifier l'ordre exact des
opérations de inject_text().
"""

import pytest

from core import injector


# ── Instrumentation ─────────────────────────────────────────────────────────

def _instrumenter(monkeypatch, ancien_contenu, is_mac=True):
    """Monkeypatche le module injector et retourne la liste des appels.

    Chaque opération est enregistrée sous forme de tuple :
      ("read",)            lecture de l'ancien presse-papiers
      ("write", texte)     écriture dans le presse-papiers
      ("paste",)           collage (Cmd+V / Ctrl+V)
      ("sleep", durée)     attente
      ("fallback", texte)  fallback de frappe (ne doit jamais être appelé ici)
    """
    appels = []

    def lire():
        appels.append(("read",))
        return ancien_contenu

    def ecrire(texte):
        appels.append(("write", texte))

    def coller():
        appels.append(("paste",))

    def dormir(duree):
        appels.append(("sleep", duree))

    def fallback(texte):
        appels.append(("fallback", texte))

    monkeypatch.setattr(injector, "IS_MAC", is_mac)
    monkeypatch.setattr(injector.time, "sleep", dormir)
    if is_mac:
        monkeypatch.setattr(injector, "_read_clipboard_mac", lire)
        monkeypatch.setattr(injector, "_clipboard_mac", ecrire)
        monkeypatch.setattr(injector, "_paste_mac", coller)
        monkeypatch.setattr(injector, "_fallback_applescript", fallback)
    else:
        monkeypatch.setattr(injector, "_read_clipboard_windows", lire)
        monkeypatch.setattr(injector, "_clipboard_windows", ecrire)
        monkeypatch.setattr(injector, "_paste_windows", coller)
        monkeypatch.setattr(injector, "_fallback_windows", fallback)
    return appels


# ── Ordre des opérations ────────────────────────────────────────────────────

def test_ordre_lecture_ecriture_collage_restauration(monkeypatch):
    """Ordre attendu : lecture ancien → écriture transcription → collage
    → restauration ancien contenu."""
    appels = _instrumenter(monkeypatch, ancien_contenu="ancien lien copié")

    injector.inject_text("bonjour transcription")

    operations = [a for a in appels if a[0] != "sleep"]
    assert operations == [
        ("read",),
        ("write", "bonjour transcription"),
        ("paste",),
        ("write", "ancien lien copié"),
    ]


def test_ordre_identique_sur_windows(monkeypatch):
    """Le chemin Windows suit exactement le même ordre d'opérations."""
    appels = _instrumenter(monkeypatch, ancien_contenu="ancien", is_mac=False)

    injector.inject_text("transcription")

    operations = [a for a in appels if a[0] != "sleep"]
    assert operations == [
        ("read",),
        ("write", "transcription"),
        ("paste",),
        ("write", "ancien"),
    ]


def test_restore_delay_attendu_entre_collage_et_restauration(monkeypatch):
    """RESTORE_DELAY est respecté APRÈS le collage et AVANT la restauration
    (laisser l'app cible consommer le collage)."""
    appels = _instrumenter(monkeypatch, ancien_contenu="ancien")

    injector.inject_text("transcription")

    i_collage      = appels.index(("paste",))
    i_restauration = appels.index(("write", "ancien"))
    assert i_collage < i_restauration
    assert ("sleep", injector.RESTORE_DELAY) in appels[i_collage:i_restauration]


# ── Ancien contenu vide / non-texte ─────────────────────────────────────────

@pytest.mark.parametrize("ancien", [None, ""])
def test_pas_de_restauration_si_ancien_vide(monkeypatch, ancien):
    """Ancien presse-papiers vide/None (ou non-texte → lecture retourne None) :
    aucune restauration — on n'écrase pas la transcription avec du vide."""
    appels = _instrumenter(monkeypatch, ancien_contenu=ancien)

    injector.inject_text("transcription")

    ecritures = [a for a in appels if a[0] == "write"]
    assert ecritures == [("write", "transcription")]
    # Pas d'attente de restauration non plus
    assert ("sleep", injector.RESTORE_DELAY) not in appels


# ── Robustesse : les erreurs ne cassent jamais l'injection ─────────────────

def test_exception_pendant_restauration_non_propagee(monkeypatch):
    """Une erreur à la restauration est loggée mais jamais propagée :
    l'injection est considérée réussie."""
    appels = _instrumenter(monkeypatch, ancien_contenu="ancien")

    def ecrire_qui_echoue_a_la_restauration(texte):
        appels.append(("write", texte))
        if texte == "ancien":
            raise RuntimeError("boom restauration")

    monkeypatch.setattr(injector, "_clipboard_mac", ecrire_qui_echoue_a_la_restauration)

    injector.inject_text("transcription")  # ne doit pas lever

    # L'injection elle-même a bien eu lieu
    assert ("write", "transcription") in appels
    assert ("paste",) in appels


def test_exception_pendant_sauvegarde_non_propagee(monkeypatch):
    """Une erreur à la lecture initiale n'empêche pas l'injection ;
    faute de sauvegarde, aucune restauration n'est tentée."""
    appels = _instrumenter(monkeypatch, ancien_contenu="ancien")

    def lire_qui_echoue():
        raise RuntimeError("boom lecture")

    monkeypatch.setattr(injector, "_read_clipboard_mac", lire_qui_echoue)

    injector.inject_text("transcription")  # ne doit pas lever

    ecritures = [a for a in appels if a[0] == "write"]
    assert ecritures == [("write", "transcription")]
    assert ("paste",) in appels


# ── Texte vide ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texte", [None, ""])
def test_texte_vide_ne_fait_rien(monkeypatch, texte):
    """inject_text sans texte : aucune opération (ni lecture, ni collage)."""
    appels = _instrumenter(monkeypatch, ancien_contenu="ancien")

    injector.inject_text(texte)

    assert appels == []
