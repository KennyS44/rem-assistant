"""Голос (синтезатор Windows или нейроголос Silero) и короткие звуковые сигналы."""
from __future__ import annotations

import io
import logging
import queue
import re
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
PREVIEW = "Рэм слушает. Сейчас восемь часов пятнадцать минут."

# Подобрано замерами (высота, разброс интонации, резкость) под образ Рем: высокий (~300 Гц),
# мягкий, с живой интонацией голос. «Ксюша» — единственный голос Silero с такой интонацией.
REM_PRESET = {"voice_engine": "silero", "silero_speaker": "kseniya",
              "voice_pitch": 20, "voice_rate": 90, "voice_timbre": 10, "voice_clips": True}


def sapi_params(pitch: int, rate: int) -> tuple[int, int]:
    """Высота (-30…+30 %) и темп (60…140 %) из настроек → шкалы SAPI (-10…+10).
    Темп 100 % — прежняя скорость Рэма (Rate = 1)."""
    p = max(-10, min(10, round(int(pitch) / 3)))
    r = max(-10, min(10, round((int(rate) - 100) / 4) + 1))
    return p, r


def timbre_params(pitch: int, rate: int, timbre: int) -> tuple[int, int, float]:
    """Тембр моложе на timbre %: синтезируем ниже и медленнее, потом ускоряем запись в k раз.
    Ускорение поднимает и высоту, и форманты; высота и темп возвращаются к заданным,
    а форманты остаются выше — голос звучит моложе, без эффекта «бурундука»."""
    k = 1 + max(0, min(20, int(timbre))) / 100
    p = max(-30, min(30, round(((1 + int(pitch) / 100) / k - 1) * 100)))
    r = max(60, min(140, round(int(rate) / k)))
    return p, r, k


def speed_up_wav(path, k: float) -> None:
    """Ускорить WAV в k раз (частота дискретизации та же) — передискретизация через FFT."""
    if k <= 1:
        return
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        a = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float64)
    n = int(len(a) / k)
    spec = np.fft.rfft(a)[: n // 2 + 1]             # отбрасываем то, что выше новой Найквиста
    b = np.fft.irfft(spec, n) * (n / len(a))
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(np.clip(b, -32768, 32767).astype("<i2").tobytes())


class Voice:
    """Озвучка в отдельном потоке. speaking — пока говорит. stop() — замолчать на полуслове
    (перебили). Всё звучит через play_wav: копия звука идёт эхоподавителю (echo), чтобы
    микрофон слышал человека поверх голоса Рэм.

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
        self.echo = None                        # aec.EchoCanceller — ему копия звука
        self.current = ""                       # что говорит сейчас
        self._said: list[tuple[float, str]] = []   # (когда закончила, фраза) — недавние
        self._stop = threading.Event()
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
        """Прослушать настройки из окна, ещё не сохраняя их. По предложению — чтобы было слышно
        и готовую запись («Рэм слушает»), и выбранный голос."""
        self.speaking.set()
        for part in re.split(r"(?<=[.!?])\s+", text.strip()):
            self.q.put(("say", part, dict(settings)))

    def wait(self, timeout: float = 15) -> None:
        """Дождаться окончания речи."""
        self.q.join()

    def recent(self, seconds: float) -> str:
        """Что она говорит сейчас и говорила за последние seconds секунд."""
        import time
        now = time.monotonic()
        return " ".join([t for end, t in self._said if now - end < seconds] + [self.current])

    def stop(self) -> None:
        """Замолчать: текущая фраза обрывается, очередь выбрасывается."""
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                break
            if item is not None and item[0] == "config":
                self.cfg = item[1]                  # настройки не теряем
            self.q.task_done()
        self._stop.set()
        if self.echo is not None and self.echo.ok:
            import time
            self.echo.silence_from(time.monotonic())

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
                self._stop.clear()
                self.current = text
                self._speak(text, {**self.cfg, **override} if override else self.cfg)
            except Exception as e:
                log.warning("ошибка озвучки: %s", e)
            finally:
                if self.current:
                    import time
                    self._said = [x for x in self._said if time.monotonic() - x[0] < 30]
                    self._said.append((time.monotonic(), self.current))
                self.current = ""
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
        if cfg.get("voice_clips"):
            from .voiceclips import clips_for
            clips = clips_for(text)
            if clips:                               # частая фраза — готовая запись голосом Рем
                for c in clips:
                    if not self._play(c):
                        break
                return
        pitch, rate = int(cfg.get("voice_pitch", 0)), int(cfg.get("voice_rate", 100))
        if cfg.get("voice_engine") == "silero" and self._speak_neural(text, cfg, pitch, rate):
            return
        if self.sapi is None:
            log.info("сказала бы: %s", text)
            return
        from xml.sax.saxutils import escape
        p, r = sapi_params(pitch, rate)
        self.sapi.Rate = r
        xml = f'<pitch absmiddle="{p}"/>{escape(text)}'
        try:                                     # в файл, а играем сами — чтобы можно было перебить
            path = self._sapi_to_wav(xml)
        except Exception as e:
            log.warning("голос Windows в файл: %s", e)
            self.sapi.AudioOutputStream = None
            self.sapi.Speak(xml, 8)               # 8 — текст с XML-разметкой
            return
        self._play(path)

    def _sapi_to_wav(self, xml: str):
        import tempfile
        import win32com.client
        path = Path(tempfile.gettempdir()) / "rem-sapi.wav"
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = 22                             # 22 кГц, 16 бит, моно
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format = fmt
        stream.Open(str(path), 3)                 # 3 — создать для записи
        try:
            self.sapi.AudioOutputStream = stream
            self.sapi.Speak(xml, 8)
        finally:
            stream.Close()
        return path

    def _play(self, path) -> bool:
        return play_wav(path, self._stop, self.echo)

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
        pitch, rate, k = timbre_params(pitch, rate, cfg.get("voice_timbre", 0))
        try:
            from .text import say_numbers
            wav = self.neural.synth(say_numbers(text), cfg.get("silero_speaker", "xenia"), pitch, rate)
            speed_up_wav(wav, k)
        except Exception as e:
            log.warning("нейроголос: %s", e)
            self.neural.close()
            self.neural = None
            return False
        self._play(wav)
        return True


def read_wav(path) -> tuple[np.ndarray, int, int]:
    """→ (int16 отсчёты, каналов, частота)."""
    with wave.open(str(path), "rb") as w:
        return (np.frombuffer(w.readframes(w.getnframes()), "<i2"), w.getnchannels(), w.getframerate())


def play_wav(path, stop: threading.Event | None = None, echo=None) -> bool:
    """Проиграть WAV и дождаться конца. stop — оборвать на полуслове (→ False).
    echo — эхоподавитель: ему копия звука с моментом, когда он прозвучит."""
    if not IS_WINDOWS:
        log.info("проиграл бы %s", path)
        return True
    try:
        return _play_stream(path, stop, echo)
    except Exception as e:                     # нет устройства вывода и т. п. — как раньше
        log.debug("проигрыватель: %s — играю через winsound", e)
    try:
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME)
    except Exception as e:
        log.warning("звук не проигрался: %s", e)
    return True


def _play_stream(path, stop, echo) -> bool:
    import time

    import sounddevice as sd

    from .aec import LEAD_S
    from .echo import to_mono_16k
    pcm, channels, rate = read_wav(path)
    pcm = pcm[: len(pcm) // channels * channels]
    ref = to_mono_16k(pcm.tobytes(), channels, rate) if echo is not None and echo.ok else None
    step = rate // 50                               # по 20 мс
    frames = len(pcm) // channels
    with sd.OutputStream(samplerate=rate, channels=channels, dtype="int16") as out:
        t0 = None
        for i in range(0, frames, step):
            if stop is not None and stop.is_set():
                out.abort()                         # выброшено и то, что уже в буфере
                return False
            out.write(pcm[i * channels:(i + step) * channels].reshape(-1, channels))
            if stop is not None and stop.is_set():
                out.abort()
                return False
            if ref is not None:
                # записанное зазвучит через out.latency; эхоподавителю копия нужна чуть раньше эха
                if t0 is None:
                    t0 = time.monotonic() + max(0.0, float(out.latency) - LEAD_S)
                a, b = round(i * 16000 / rate), round(min(i + step, frames) * 16000 / rate)
                echo.played(ref[a:b], t0 + a / 16000)
    return True                                     # выход из with дожидается хвоста буфера
