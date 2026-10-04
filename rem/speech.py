"""Голос (встроенный синтезатор Windows, SAPI) и короткие звуковые сигналы."""
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


class Voice:
    """Озвучка в отдельном потоке. speaking — пока говорит (микрофон в это время глухой)."""

    def __init__(self, voice_name: str = ""):
        self.voice_name = voice_name
        self.q: queue.Queue[str | None] = queue.Queue()
        self.speaking = threading.Event()
        self.voices: list[str] = []
        threading.Thread(target=self._run, name="voice", daemon=True).start()

    def say(self, text: str) -> None:
        if text:
            self.speaking.set()
            self.q.put(text)

    def wait(self, timeout: float = 15) -> None:
        """Дождаться окончания речи."""
        self.q.join()

    def _run(self) -> None:
        sapi = None
        if IS_WINDOWS:
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                sapi = win32com.client.Dispatch("SAPI.SpVoice")
                tokens = sapi.GetVoices()
                self.voices = [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
                pick = None
                for i in range(tokens.Count):
                    desc = tokens.Item(i).GetDescription()
                    lang = tokens.Item(i).GetAttribute("Language") or ""
                    if self.voice_name and self.voice_name.lower() in desc.lower():
                        pick = i
                        break
                    if pick is None and ("419" in lang or "russian" in desc.lower() or "irina" in desc.lower()):
                        pick = i                       # 419 — код русского языка
                if pick is not None:
                    sapi.Voice = tokens.Item(pick)
                sapi.Rate = 1
            except Exception as e:
                log.warning("голос Windows недоступен: %s", e)
                sapi = None
        while True:
            text = self.q.get()
            try:
                if text is None:
                    return
                if sapi is not None:
                    sapi.Speak(text, 0)                # синхронно в своём потоке
                else:
                    log.info("сказала бы: %s", text)
            except Exception as e:
                log.warning("ошибка озвучки: %s", e)
            finally:
                self.q.task_done()
                if self.q.empty():
                    self.speaking.clear()
