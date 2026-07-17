import sys
import os
import json
import logging
import threading

logging.basicConfig(level=logging.WARNING, format="%(name)s [%(levelname)s] %(message)s")

IS_MAC = sys.platform == "darwin"

CONFIG_DIR  = os.path.expanduser("~/.voxaho")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "language":        "fr",
    "model":           "small",
    "reformatting":    True,
    "first_run":       True,
    "bar_x":           None,
    "bar_y":           None,
    "win_key":         "ctrl_r",   # Windows uniquement
    "input_device":    None,       # index micro (None = périphérique système par défaut)
    "beam_size":       1,          # faster-whisper beam search (1 = greedy, le plus rapide)
    "compute_backend": "auto",     # "auto" | "cpu" | "mlx" (fixé au constructeur du Transcriber)
    "ai_reformat":     False,      # reformatage IA local via LLM (retombe sur les règles si LLM absent)
    "translate_to":    None,       # traduction à la volée : code langue cible (None = pas de traduction)
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logging.warning(f"Config corrompue ({e}), réinitialisation.")
        return None


def save_config(config: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def main():
    # macOS uniquement — masquer l'icône Dock
    if IS_MAC:
        try:
            from AppKit import NSApp, NSApplicationActivationPolicyAccessory
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except ImportError:
            pass
        except Exception as e:
            logging.warning(f"setActivationPolicy: {e}")

    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Voxaho")

    config = load_config()

    if config is None or config.get("first_run", True):
        from ui.setup_wizard import SetupWizard
        from PyQt6.QtWidgets import QDialog
        wizard = SetupWizard(save_config)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            fallback = {**DEFAULT_CONFIG, "first_run": False}
            save_config(fallback)
            sys.exit(0)
        config = load_config()

    if config:
        # ── Gate licence / trial ─────────────────────────────────────────────
        from core import license as license_mod

        if not _ensure_licensed_or_trial(app, license_mod):
            sys.exit(0)

        from ui.floating_bar import FloatingBar
        bar = FloatingBar(config, save_config)
        bar.show()
        sys.exit(app.exec())


def _ensure_licensed_or_trial(app, license_mod) -> bool:
    """Garantit qu'on a soit une licence soit un trial actif.

    Retourne False si l'utilisateur refuse d'activer ET refuse le trial,
    auquel cas l'app doit quitter.
    """
    # Cas 1 : déjà licencié → on continue.
    if license_mod.is_activated():
        # Re-validation périodique en ARRIÈRE-PLAN (thread daemon) : l'appel
        # réseau Lemon Squeezy a un timeout de 10 s et ne doit jamais retarder
        # le démarrage. Le résultat n'affecte que le prochain lancement : si la
        # clé s'avère révoquée/remboursée, revalidate_if_due() purge la licence
        # stockée et le gate licence/trial reprendra la main au démarrage suivant.
        threading.Thread(
            target=license_mod.revalidate_if_due,
            name="voxaho-license-revalidate",
            daemon=True,
        ).start()
        return True

    # Cas 2 : trial encore valide → on continue.
    if license_mod.is_licensed_or_trial_ok():
        return True

    # Cas 3 : pas de licence, pas de trial → premier lancement.
    if not license_mod.has_trial():
        license_mod.start_trial()
        logging.info("Trial 14 jours démarré.")
        return True

    # Cas 4 : trial expiré, pas de licence → dialog bloquant.
    from ui.setup_wizard import LicenseDialog
    from PyQt6.QtWidgets import QDialog, QMessageBox
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtCore import QUrl

    msg = QMessageBox()
    msg.setWindowTitle("Voxaho — Trial expiré")
    msg.setText(
        "Votre essai de 14 jours est terminé.\n\n"
        "Entrez une clé de licence pour continuer, ou achetez Voxaho."
    )
    msg.setIcon(QMessageBox.Icon.Information)
    enter_btn = msg.addButton("Entrer une clé", QMessageBox.ButtonRole.AcceptRole)
    buy_btn   = msg.addButton("Acheter",        QMessageBox.ButtonRole.ActionRole)
    quit_btn  = msg.addButton("Quitter",        QMessageBox.ButtonRole.RejectRole)
    msg.exec()

    clicked = msg.clickedButton()
    if clicked is buy_btn:
        QDesktopServices.openUrl(QUrl("https://voxaho.com/#tarifs"))
        # Après ouverture du navigateur, on présente quand même le dialog d'activation.
    elif clicked is quit_btn:
        return False

    dlg = LicenseDialog(allow_trial=False)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    return license_mod.is_activated()


if __name__ == "__main__":
    main()
