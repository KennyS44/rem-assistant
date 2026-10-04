"""Голос (синтезатор Windows или нейроголос Silero) и короткие звуковые сигналы."""
from __future__ import annotations

import io
import logging
import queue
import sys
import threading
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("rem.speech")
IS_WINDOWS = sys.platform == "win32"


def _tone_wav(parts: list[tuple[float, float]], volume: float = 0.25, rate: int = 22050) -> bytes:
    """Последовательность (частота Гц, длительность с) → WAV с мягкими краями."""
    out = []
    for freq, dur in parts:
        t = np.arange(int(rate * dur)) / rate
        tone = np.sin(2 * np.pi * freq * t) if freq else np.zeros_like(t)
        fade = np.minimum(1, np.minimum(t, dur - t) / 0.012)          # 12 мс плавности
        out.append(tone * fade)
    pcm = (np.concatenate(out) * volume * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


SOUNDS = {
    "wake":  [(660, 0.07), (0, 0.02), (880, 0.09)],    # слушаю
    "done":  [(990, 0.06)],                            # готово
    "error": [(330, 0.12), (0, 0.03), (262, 0.16)],   # не получилось
    "timer": [(880, 0.15), (0, 0.1)] * 4,             # таймер
}


class Sounds:
    def __init__(self, folder: Path):
        self.files = {}
        folder.mkdir(parents=True, exist_ok=True)
        for name, parts in SOUNDS.items():
            p = folder / f"{name}.wav"
            p.write_bytes(_tone_wav(parts))
            self.files[name] = str(p)

    def play(self, name: str) -> None:
        if not IS_WINDOWS:
            log.info("звук: %s", name)
            return
        import winsound
        winsound.PlaySound(self.files[name], winsound.SND_FILENAME | winsound.SND_ASYNC)


SILERO_VOICES = {"xenia": "Ксения", "baya": "Байя", "kseniya": "Ксюша",
                 "aidar": "Айдар (мужской)", "eugene": "Евгений (мужской)"}
PREVIEW = "Рэм слушает. Таймер на пять минут поставлен."


def sapi_params(pitch: int, rate: int) -> tuple[int, int]:
    """Высота (-30…+30 %) и темп (60…140 %) из настроек → шкалы SAPI (-10…+10).
    Темп 100 % — прежняя скорость Рэма (Rate = 1)."""
    p = max(-10, min(10, round(int(pitch) / 3)))
    r = max(-10, min(10, round((int(rate) - 100) / 4) + 1))
    return p, r


class Voice:
    """Озвучка в отдельном потоке. speaking — пока говорит (микрофон в это время глухой).

    Два движка: голос Windows (SAPI) и нейроголос Silero (отдельная программа RemVoice,
    см. voicepack.py). Если нейроголос не запустился — говорим голосом Windows.
    """

    def __init__(self, voice_name: str = "", config: dict | None = None):
        self.cfg = dict(config or {})
        if voice_name:
            self.cfg.setdefault("voice", voice_name)
        self.q: queue.Queue = queue.Queue()
        self.speaking = threading.Event()
        self.voices: list[str] = []
        self.ready = threading.Event()
        self.neural = None                      # voicepack.NeuralVoice, когда включён
        self.on_problem = lambda text: None     # сообщить человеку (уведомление в трее)
        threading.Thread(target=self._run, name="voice", daemon=True).start()

    def configure(self, config: dict) -> None:
        """Новые настройки голоса — применяются к следующей фразе."""
        self.q.put(("config", dict(config)))

    def say(self, text: str) -> None:
        if text:
            self.speaking.set()
            self.q.put(("say", text, None))

    def preview(self, settings: dict, text: str = PREVIEW) -> None:
        """Прослушать настройки из окна, ещё не сохраняя их."""
        self.speaking.set()
        self.q.put(("say", text, dict(settings)))

    def wait(self, timeout: float = 15) -> None:
        """Дождаться окончания речи."""
        self.q.join()

    # ——— поток озвучки ———

    def _run(self) -> None:
        self.sapi = None
        if IS_WINDOWS:
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                self.sapi = win32com.client.Dispatch("SAPI.SpVoice")
                tokens = self.sapi.GetVoices()
                self.voices = [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
            except Exception as e:
                log.warning("голос Windows недоступен: %s", e)
                self.sapi = None
        self._apply_sapi_voice(self.cfg.get("voice", ""))
        self.ready.set()
        while True:
            item = self.q.get()
            try:
                if item is None:
                    return
                if item[0] == "config":
                    self.cfg = item[1]
                    self._apply_sapi_voice(self.cfg.get("voice", ""))
                    if self.cfg.get("voice_engine") != "silero" and self.neural:
                        self.neural.close()
                        self.neural = None
                    continue
                _, text, override = item
                self._speak(text, {**self.cfg, **override} if override else self.cfg)
            except Exception as e:
                log.warning("ошибка озвучки: %s", e)
            finally:
                self.q.task_done()
                if self.q.empty():
                    self.speaking.clear()

    def _apply_sapi_voice(self, name: str) -> None:
        if self.sapi is None:
            return
        tokens = self.sapi.GetVoices()
        pick = None
        for i in range(tokens.Count):
            desc = tokens.Item(i).GetDescription()
            lang = tokens.Item(i).GetAttribute("Language") or ""
            if name and name.lower() in desc.lower():
                pick = i
                break
            if pick is None and ("419" in lang or "russian" in desc.lower() or "irina" in desc.lower()):
                pick = i                           # 419 — код русского языка
        if pick is not None:
            self.sapi.Voice = tokens.Item(pick)

    def _speak(self, text: str, cfg: dict) -> None:
        pitch, rate = int(cfg.get("voice_pitch", 0)), int(cfg.get("voice_rate", 100))
        if cfg.get("voice_engine") == "silero" and self._speak_neural(text, cfg, pitch, rate):
            return
        if self.sapi is None:
            log.info("сказала бы: %s", text)
            return
        from xml.sax.saxutils import escape
        p, r = sapi_params(pitch, rate)
        self.sapi.Rate = r
        self.sapi.Speak(f'<pitch absmiddle="{p}"/>{escape(text)}', 8)   # 8 — текст с XML-разметкой

    def _speak_neural(self, text: str, cfg: dict, pitch: int, rate: int) -> bool:
        from . import voicepack
        if self.neural is None:
            if not voicepack.ready():
                return False
            try:
                self.neural = voicepack.NeuralVoice()
            except Exception as e:
                log.warning("нейроголос не запустился: %s", e)
                self.on_problem("Нейроголос не запустился — говорю голосом Windows. Подробности в rem.log.")
                return False
        try:
            wav = self.neural.synth(text, cfg.get("silero_speaker", "xenia"), pitch, rate)
        except Exception as e:
            log.warning("нейроголос: %s", e)
            self.neural.close()
            self.neural = None
            return False
        play_wav(wav)
        return True


def play_wav(path) -> None:
    """Проиграть WAV и дождаться конца."""
    if not IS_WINDOWS:
        log.info("проиграл бы %s", path)
        return
    import winsound
    winsound.PlaySound(str(path), winsound.SND_FILENAME)
