import sys
import textwrap
import types

import numpy as np
import pytest

from rem import update, voicepack
from rem.echo import SpeakerTap, to_mono_16k
from rem.listen import is_real_mic, pick_input, wake_from_speakers
from rem.speech import sapi_params


# ——— микрофон ———

def fake_sd(monkeypatch, devices, default_input):
    sd = types.SimpleNamespace()
    sd.default = types.SimpleNamespace(hostapi=0)
    sd.query_devices = lambda kind=None: devices[default_input] if kind == "input" else devices
    monkeypatch.setitem(sys.modules, "sounddevice", sd)


DEVICES = [
    {"name": "Microsoft Sound Mapper - Input", "max_input_channels": 2, "hostapi": 0},
    {"name": "Переназначение звуковых устр. - Input", "max_input_channels": 2, "hostapi": 0},
    {"name": "Стерео микшер (Realtek(R) Audio)", "max_input_channels": 2, "hostapi": 0},
    {"name": "Микрофон (USB Audio Device)", "max_input_channels": 1, "hostapi": 0},
    {"name": "Динамики (Realtek(R) Audio)", "max_input_channels": 0, "hostapi": 0},
    {"name": "Микрофон (USB Audio Device)", "max_input_channels": 1, "hostapi": 1},   # тот же, WASAPI
]


def test_not_mic_names():
    assert not is_real_mic("Стерео микшер (Realtek(R) Audio)")
    assert not is_real_mic("Stereo Mix (Realtek High Definition Audio)")
    assert not is_real_mic("CABLE Output (VB-Audio Virtual Cable)")
    assert is_real_mic("Микрофон (USB Audio Device)")
    assert is_real_mic("Headset Microphone (Arctis 7 Chat)")


def test_mapper_is_not_listed(monkeypatch):
    from rem.listen import input_devices
    fake_sd(monkeypatch, DEVICES, 3)
    assert [n for _, n in input_devices()] == ["Стерео микшер (Realtek(R) Audio)", "Микрофон (USB Audio Device)"]


def test_default_mic_left_to_windows(monkeypatch):
    fake_sd(monkeypatch, DEVICES, 3)
    assert pick_input(None) is None


def test_stereo_mix_as_default_is_skipped(monkeypatch):
    fake_sd(monkeypatch, DEVICES, 2)
    assert pick_input(None) == 3


def test_chosen_mic_by_name(monkeypatch):
    fake_sd(monkeypatch, DEVICES, 2)
    assert pick_input("Микрофон (USB Audio Device)") == 3
    assert pick_input("Нет такого") == 3              # пропал — берём настоящий микрофон


# ——— колонки ———

def test_mono_16k_from_44100():
    a = np.zeros(4410 * 2, dtype=np.int16).tobytes()
    assert abs(len(to_mono_16k(a, 2, 44100)) - 1600) <= 1


def test_tap_keeps_recent():
    tap = SpeakerTap()
    assert tap.recent(1) is None
    tap.feed(np.ones(1600, dtype=np.int16))
    assert len(tap.recent(1)) == 1600


class FakeASR:
    def __init__(self, text):
        self.text = text

    def recognize(self, audio):
        return self.text


@pytest.mark.parametrize("text,expected", [
    ("Рем, подожди меня!", True),
    ("И тут пришла Рем.", True),            # в фильме слово бывает где угодно
    ("Сегодня отличная погода.", False),
    ("Мы пошли с тремя друзьями.", False),
])
def test_wake_from_speakers(text, expected):
    loud = np.full(16000, 3000, dtype=np.int16)
    assert wake_from_speakers(FakeASR(text), loud, "рэм") is expected


def test_quiet_speakers_not_checked():
    asr = FakeASR("Рем")
    assert wake_from_speakers(asr, np.zeros(16000, dtype=np.int16), "рэм") is False
    assert wake_from_speakers(asr, None, "рэм") is False


# ——— голос ———

def test_sapi_params():
    assert sapi_params(0, 100) == (0, 1)              # прежняя скорость Рэма
    assert sapi_params(15, 80) == (5, -4)
    assert sapi_params(-99, 999) == (-10, 10)


FAKE_VOICE = textwrap.dedent('''
    import json, sys, wave
    print(json.dumps({"ready": True, "speakers": ["xenia"]}), flush=True)
    for line in sys.stdin:
        r = json.loads(line)
        if r["speaker"] != "xenia":
            print(json.dumps({"ok": False, "error": "нет голоса"}), flush=True)
            continue
        with wave.open(r["out"], "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\\0\\0" * 2400)
        print(json.dumps({"ok": True, "seconds": 0.1}), flush=True)
''')


def test_neural_voice_protocol(tmp_path, monkeypatch):
    monkeypatch.setattr(voicepack, "voice_dir", lambda: tmp_path)
    script = tmp_path / "fake_voice.py"
    script.write_text(FAKE_VOICE, encoding="utf-8")
    v = voicepack.NeuralVoice([sys.executable, str(script)], start_timeout=20)
    try:
        assert v.speakers == ["xenia"]
        wav = v.synth("Рэм слушает", "xenia", 10, 90)
        assert wav.exists() and wav.stat().st_size > 4000
        with pytest.raises(RuntimeError, match="нет голоса"):
            v.synth("текст", "baya")
    finally:
        v.close()


def test_neural_voice_failed_start(tmp_path, monkeypatch):
    monkeypatch.setattr(voicepack, "voice_dir", lambda: tmp_path)
    script = tmp_path / "broken.py"
    script.write_text('print(\'{"ready": false, "error": "модель повреждена"}\', flush=True)', encoding="utf-8")
    with pytest.raises(RuntimeError, match="модель повреждена"):
        voicepack.NeuralVoice([sys.executable, str(script)], start_timeout=20)


# ——— обновления ———

def test_versions():
    assert update.newer("0.2.0", "0.1.2")
    assert update.newer("v0.10.0", "0.9.0")
    assert not update.newer("0.1.2", "0.1.2")
    assert not update.newer("voice-1", "0.1.0")       # релиз голоса — не версия программы


def test_release_without_installer():
    assert update.from_api({"tag_name": "voice-1", "assets": [{"name": "RemVoice.zip"}]}) is None


def test_cleanup_keeps_only_newer(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "update_dir", lambda: tmp_path)
    monkeypatch.setattr(update, "__version__", "0.2.0")
    for n in ("RemSetup-0.1.1.exe", "RemSetup-0.2.0.exe", "RemSetup-0.3.0.exe", "RemSetup-0.3.0.exe.part"):
        (tmp_path / n).write_bytes(b"x")
    update.cleanup()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["RemSetup-0.3.0.exe"]
