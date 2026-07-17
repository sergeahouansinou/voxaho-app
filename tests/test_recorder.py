"""
Tests du Recorder (Phase 0) : sélection du périphérique d'entrée.

On ne touche JAMAIS au vrai matériel audio : sounddevice est remplacé par un
double factice via monkeypatch. On vérifie :
- list_input_devices() : filtrage input-only + flag `default` ;
- Recorder(device=...) : le device passé est bien stocké.
"""

import sys
import types
from pathlib import Path

# Rendre le package `core` importable quel que soit le mode d'invocation de pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import core.recorder as recorder
from core.recorder import Recorder, list_input_devices


# Liste factice de périphériques : entrées, sorties et un mixte in/out.
FAKE_DEVICES = [
    {"name": "MacBook Microphone",     "max_input_channels": 1, "max_output_channels": 0},
    {"name": "MacBook Speakers",       "max_input_channels": 0, "max_output_channels": 2},
    {"name": "Casque USB (in+out)",    "max_input_channels": 2, "max_output_channels": 2},
    {"name": "Sortie HDMI",            "max_input_channels": 0, "max_output_channels": 6},
    {"name": "Micro Bluetooth",        "max_input_channels": 1, "max_output_channels": 0},
]


def _make_fake_sd(devices, default_input_index):
    """Construit un faux module sounddevice minimal."""
    fake = types.SimpleNamespace()
    fake.query_devices = lambda: devices
    fake.default = types.SimpleNamespace(device=[default_input_index, 1])
    return fake


# ── list_input_devices : filtrage input-only + default ───────────────────────

class TestListInputDevices:
    def test_filtre_input_only(self, monkeypatch):
        monkeypatch.setattr(recorder, "sd", _make_fake_sd(FAKE_DEVICES, 0))
        devices = list_input_devices()
        # Seuls les 3 périphériques avec max_input_channels > 0 sont retenus
        names = [d["name"] for d in devices]
        assert names == ["MacBook Microphone", "Casque USB (in+out)", "Micro Bluetooth"]
        # Les sorties pures sont exclues
        assert "MacBook Speakers" not in names
        assert "Sortie HDMI" not in names

    def test_index_original_preserve(self, monkeypatch):
        monkeypatch.setattr(recorder, "sd", _make_fake_sd(FAKE_DEVICES, 0))
        devices = list_input_devices()
        # Les index correspondent à la position dans la liste sounddevice d'origine
        by_name = {d["name"]: d["index"] for d in devices}
        assert by_name["MacBook Microphone"] == 0
        assert by_name["Casque USB (in+out)"] == 2
        assert by_name["Micro Bluetooth"] == 4

    def test_flag_default(self, monkeypatch):
        # Le micro par défaut est l'index 2 (Casque USB)
        monkeypatch.setattr(recorder, "sd", _make_fake_sd(FAKE_DEVICES, 2))
        devices = list_input_devices()
        defaults = [d for d in devices if d["default"]]
        assert len(defaults) == 1
        assert defaults[0]["name"] == "Casque USB (in+out)"
        assert defaults[0]["index"] == 2

    def test_structure_des_dicts(self, monkeypatch):
        monkeypatch.setattr(recorder, "sd", _make_fake_sd(FAKE_DEVICES, 0))
        for d in list_input_devices():
            assert set(d.keys()) == {"index", "name", "default"}
            assert isinstance(d["index"], int)
            assert isinstance(d["name"], str)
            assert isinstance(d["default"], bool)

    def test_tolerant_si_query_devices_leve(self, monkeypatch):
        def boom():
            raise RuntimeError("sounddevice indisponible")

        fake = types.SimpleNamespace(query_devices=boom,
                                     default=types.SimpleNamespace(device=[None, None]))
        monkeypatch.setattr(recorder, "sd", fake)
        # Ne doit pas lever, renvoie une liste vide
        assert list_input_devices() == []

    def test_tolerant_si_default_indisponible(self, monkeypatch):
        # sd.default.device qui lève → aucun device marqué default, mais liste OK
        class BadDefault:
            @property
            def device(self):
                raise RuntimeError("pas de default")

        fake = types.SimpleNamespace(query_devices=lambda: FAKE_DEVICES,
                                     default=BadDefault())
        monkeypatch.setattr(recorder, "sd", fake)
        devices = list_input_devices()
        assert len(devices) == 3
        assert all(d["default"] is False for d in devices)


# ── Recorder : stockage du device ────────────────────────────────────────────

class TestRecorderDevice:
    def test_device_par_defaut_none(self):
        r = Recorder()
        assert r._device is None

    def test_device_stocke(self):
        r = Recorder(device=3)
        assert r._device == 3

    def test_device_zero_stocke(self):
        # 0 est un index valide (ne doit pas être confondu avec None)
        r = Recorder(device=0)
        assert r._device == 0
