"""
Interception de la touche déclencheur selon la plateforme.

  macOS   → CGEventTap (Quartz/PyObjC) — touche Fn
  Windows → pynput Listener            — touche Ctrl Droit (configurable),
            avec suppression sélective via win32_event_filter pour que la
            touche de dictée ne fuie pas vers l'application active (M2/M9)

Requiert sur macOS : permission Accessibilité dans les Réglages Système.
Requiert sur Windows : aucun droit admin (pynput fonctionne en user).
"""

import sys
import threading
import logging
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"

# Label affiché dans l'UI selon la plateforme
HOTKEY_LABEL     = "Fn"         if IS_MAC else "Ctrl Droit"
HOTKEY_HINT      = "Maintenir Fn" if IS_MAC else "Maintenir Ctrl ▶"

# Correspondance nom config → Virtual-Key Code Windows (indépendant de pynput,
# donc importable/testable sur toute plateforme). Utilisé par win32_event_filter
# pour identifier la touche cible via data.vkCode.
WIN_VK_CODES = {
    "ctrl_r":    0xA3,  # VK_RCONTROL
    "ctrl_l":    0xA2,  # VK_LCONTROL
    "alt_r":     0xA5,  # VK_RMENU (Alt droite / AltGr)
    "shift_r":   0xA1,  # VK_RSHIFT
    "caps_lock": 0x14,  # VK_CAPITAL
}

# Messages clavier Windows reçus par le hook bas niveau (WM_*)
WM_KEYDOWN    = 0x0100
WM_KEYUP      = 0x0101
WM_SYSKEYDOWN = 0x0104  # touche pressée avec Alt maintenu
WM_SYSKEYUP   = 0x0105  # touche relâchée avec Alt maintenu

NX_SECONDARYFNMASK = 0x800000  # flag Fn dans CGEventFlags (macOS)


class HotkeyListener(QObject):
    fn_pressed          = pyqtSignal()
    fn_released         = pyqtSignal()
    permission_error    = pyqtSignal()
    permission_restored = pyqtSignal()   # émis quand CGEventTap fonctionne enfin

    def __init__(self, win_key: str = "ctrl_r"):
        super().__init__()
        self._fn_active    = False
        self._fn_lock      = threading.Lock()
        self._should_stop  = False
        self._thread       = None
        self._run_loop_ref = None          # macOS
        self._run_loop_ready = threading.Event()  # macOS — set quand _run_loop_ref est assigné
        self._win_listener = None          # Windows
        self._win_key_name = win_key       # Windows — nom de la touche

    # ── Démarrage / arrêt ─────────────────────────────────────────────────

    def start(self):
        self._should_stop = False
        self._run_loop_ready.clear()
        target = self._run_loop_mac if IS_MAC else self._run_loop_win
        self._thread = threading.Thread(target=target, daemon=True, name="HotkeyLoop")
        self._thread.start()

    def stop(self):
        self._should_stop = True
        if IS_MAC:
            try:
                from CoreFoundation import CFRunLoopStop
                # Attendre que _run_loop_mac ait assigné _run_loop_ref (timeout court
                # pour éviter blocage si le run loop n'a jamais démarré).
                if self._run_loop_ready.wait(timeout=1.0) and self._run_loop_ref:
                    CFRunLoopStop(self._run_loop_ref)
            except Exception:
                pass
        else:
            if self._win_listener:
                self._win_listener.stop()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    # ── macOS — CGEventTap ────────────────────────────────────────────────

    def _run_loop_mac(self):
        """
        Auto-réparant : si CGEventTap échoue (permission Accessibilité manquante),
        on retente toutes les 3 s. Dès que la permission est accordée, on rentre
        dans la run loop et on émet `permission_restored` pour que l'UI sorte
        de l'état d'erreur.

        Double vérification :
          1. AXIsProcessTrusted() — API officielle Apple, source de vérité
          2. CGEventTapCreate() — création effective du tap
        """
        import time

        try:
            from Quartz import (
                CGEventTapCreate, CGEventMaskBit,
                kCGHIDEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault,
                kCGEventFlagsChanged, CGEventGetFlags,
                CFMachPortCreateRunLoopSource, CFRunLoopAddSource,
                CFRunLoopGetCurrent, CFRunLoopRun, kCFRunLoopDefaultMode,
            )
        except ImportError as e:
            logger.error(f"Quartz indisponible : {e}")
            self.permission_error.emit()
            return

        # AXIsProcessTrusted : vérité officielle macOS sur la permission Accessibilité
        # AXIsProcessTrustedWithOptions(prompt=True) : force le popup système de demande
        try:
            from ApplicationServices import (
                AXIsProcessTrusted,
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )
            _ax_check = AXIsProcessTrusted
            _ax_prompt_options = {kAXTrustedCheckOptionPrompt: True}
            _ax_prompt = lambda: AXIsProcessTrustedWithOptions(_ax_prompt_options)
        except ImportError:
            _ax_check = None
            _ax_prompt = None
            logger.warning("ApplicationServices indisponible — fallback sur CGEventTapCreate")

        # Au tout premier appel : force le prompt système pour que macOS affiche
        # le dialog "Voxaho voudrait contrôler cet ordinateur via Accessibilité"
        # qui propose un bouton "Ouvrir les Réglages Système".
        _first_prompt_done = False

        def _callback(proxy, event_type, event, refcon):
            if self._should_stop:
                return event
            if event_type == kCGEventFlagsChanged:
                flags  = CGEventGetFlags(event)
                fn_now = bool(flags & NX_SECONDARYFNMASK)
                with self._fn_lock:
                    if fn_now and not self._fn_active:
                        self._fn_active = True
                        self.fn_pressed.emit()
                        return None
                    if not fn_now and self._fn_active:
                        self._fn_active = False
                        self.fn_released.emit()
                        return None
            return event

        mask = CGEventMaskBit(kCGEventFlagsChanged)
        error_emitted = False

        while not self._should_stop:
            # 1. Vérif officielle Apple : sommes-nous trusted pour Accessibilité ?
            if _ax_check is not None and not _ax_check():
                if not error_emitted:
                    logger.warning(
                        "AXIsProcessTrusted = False → Accessibilité non accordée. "
                        "Réglages Système > Confidentialité > Accessibilité : activez Voxaho. "
                        "Voxaho retentera automatiquement toutes les 3 s."
                    )
                    self.permission_error.emit()
                    error_emitted = True
                    # Force le popup macOS la première fois — déclenche
                    # le dialog système avec bouton "Ouvrir les Réglages"
                    if _ax_prompt is not None and not _first_prompt_done:
                        try:
                            _ax_prompt()
                            _first_prompt_done = True
                            logger.info("Popup macOS Accessibilité demandé")
                        except Exception as e:
                            logger.warning(f"_ax_prompt: {e}")
                for _ in range(30):
                    if self._should_stop:
                        return
                    time.sleep(0.1)
                continue

            # 2. Tentative de création du tap CGEventTap
            try:
                tap = CGEventTapCreate(
                    kCGHIDEventTap, kCGHeadInsertEventTap,
                    kCGEventTapOptionDefault, mask, _callback, None,
                )
            except Exception as e:
                logger.error(f"CGEventTapCreate: {e}", exc_info=True)
                tap = None

            if tap is None:
                if not error_emitted:
                    logger.warning(
                        "CGEventTapCreate=None malgré AX trusted — état macOS inattendu. "
                        "Retry dans 3 s."
                    )
                    self.permission_error.emit()
                    error_emitted = True
                for _ in range(30):
                    if self._should_stop:
                        return
                    time.sleep(0.1)
                continue

            # Tap créé avec succès
            if error_emitted:
                logger.info("Accessibilité accordée — hotkey opérationnel")
                self.permission_restored.emit()
                error_emitted = False

            try:
                source = CFMachPortCreateRunLoopSource(None, tap, 0)
                loop   = CFRunLoopGetCurrent()
                self._run_loop_ref = loop
                self._run_loop_ready.set()
                CFRunLoopAddSource(loop, source, kCFRunLoopDefaultMode)
                CFRunLoopRun()
            except Exception as e:
                logger.error(f"Run loop macOS: {e}", exc_info=True)

            # Run loop terminée (stop appelé ou erreur)
            self._run_loop_ref = None
            self._run_loop_ready.clear()
            return

    # ── Windows — pynput ──────────────────────────────────────────────────
    #
    # ⚠ À valider sur Windows avant release (non exécutable sur macOS).
    #
    # Suppression sélective de la touche de dictée (M2/M9) :
    # sans suppression, maintenir la touche pendant la dictée la laisse fuir
    # vers l'application active (Ctrl+clic accidentels, raccourcis déclenchés,
    # et l'option caps_lock BASCULE Verr. Maj à chaque appui). On utilise
    # `win32_event_filter`, appelé par pynput dans le hook clavier bas niveau
    # AVANT que l'événement n'atteigne les autres applications : quand la
    # touche cible est détectée (data.vkCode), on émet fn_pressed/fn_released
    # puis on appelle `listener.suppress_event()` pour avaler l'événement.

    def _handle_target_press(self):
        """Appui touche cible — anti-répétition via _fn_active sous _fn_lock.

        Windows envoie des WM_KEYDOWN répétés tant que la touche est maintenue
        (auto-repeat) : _fn_active garantit une seule émission de fn_pressed.
        """
        if self._should_stop:
            return
        with self._fn_lock:
            if not self._fn_active:
                self._fn_active = True
                self.fn_pressed.emit()

    def _handle_target_release(self):
        """Relâchement touche cible — symétrique de _handle_target_press."""
        if self._should_stop:
            return
        with self._fn_lock:
            if self._fn_active:
                self._fn_active = False
                self.fn_released.emit()

    def _run_loop_win(self):
        try:
            from pynput import keyboard as kb

            # vkCode de la touche cible (identification dans le filter)
            target_vk = WIN_VK_CODES.get(
                self._win_key_name, WIN_VK_CODES["ctrl_r"]
            )
            # Objet Key pynput correspondant (utilisé par le fallback sans
            # suppression, où seuls on_press/on_release voient les touches)
            key_map = {
                "ctrl_r":    kb.Key.ctrl_r,
                "ctrl_l":    kb.Key.ctrl_l,
                "alt_r":     kb.Key.alt_r,
                "shift_r":   kb.Key.shift_r,
                "caps_lock": kb.Key.caps_lock,
            }
            target_key = key_map.get(self._win_key_name, kb.Key.ctrl_r)

            # Filet de sécurité : win32_event_filter + suppress_event existent
            # depuis pynput 1.4. Sur une version plus ancienne, on retombe sur
            # le comportement historique SANS suppression (la touche fuit vers
            # l'app active, mais la dictée reste fonctionnelle).
            supports_suppression = hasattr(kb.Listener, "suppress_event")

            def _win32_event_filter(msg, data):
                """Hook bas niveau : intercepte la touche cible avant les apps.

                Reçoit (msg, data) où data est un KBDLLHOOKSTRUCT ;
                data.vkCode identifie la touche physique.
                """
                if data.vkCode != target_vk:
                    return True  # autres touches : laisser passer normalement
                if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    self._handle_target_press()
                elif msg in (WM_KEYUP, WM_SYSKEYUP):
                    self._handle_target_release()
                # ⚠ suppress_event() supprime l'événement en levant une
                # exception interne pynput → TOUJOURS l'appeler en DERNIER.
                # C'est ce qui empêche la touche d'atteindre les autres apps
                # (et corrige le toggle Verr. Maj pour l'option caps_lock).
                self._win_listener.suppress_event()

            # Callbacks on_press/on_release :
            #   - mode suppression : la touche cible étant avalée par le filter,
            #     ils ne la voient jamais — ils ne servent que de filet
            #     (no-op pour les autres touches, et redondance inoffensive
            #     pour la cible grâce à l'anti-répétition de _handle_target_*).
            #   - mode fallback (vieux pynput) : ils portent toute la logique.
            def on_press(key):
                if self._should_stop:
                    return False
                if key == target_key:
                    self._handle_target_press()

            def on_release(key):
                if self._should_stop:
                    return False
                if key == target_key:
                    self._handle_target_release()

            if supports_suppression:
                try:
                    self._win_listener = kb.Listener(
                        on_press=on_press,
                        on_release=on_release,
                        win32_event_filter=_win32_event_filter,
                    )
                except TypeError as e:
                    # Constructeur qui refuse le kwarg (pynput exotique) —
                    # ne pas tuer le hotkey pour autant, retomber en fallback
                    logger.warning(f"win32_event_filter refusé par pynput: {e}")
                    supports_suppression = False

            if not supports_suppression:
                logger.warning(
                    "pynput sans win32_event_filter/suppress_event — la touche "
                    "de dictée ne sera PAS supprimée et fuira vers "
                    "l'application active. Mettez à jour : pip install -U pynput"
                )
                self._win_listener = kb.Listener(
                    on_press=on_press, on_release=on_release
                )

            self._win_listener.start()
            self._win_listener.join()

        except ImportError:
            logger.error("pynput absent. Lancez : pip install pynput")
            self.permission_error.emit()
        except Exception as e:
            logger.error(f"HotkeyListener Windows: {e}", exc_info=True)
            self.permission_error.emit()
