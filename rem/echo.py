"""Что сейчас играет в колонках — чтобы не путать фильм с человеком.

Windows умеет отдавать копию звука, идущего на устройство вывода (WASAPI loopback).
Храним последние 15 секунд; когда сторож услышал «Рэм», Listener сверяет
микрофон с колонками: если слово было и там — это видео, а не обращение.

В наушниках микрофон колонок не слышит, тогда проверка просто ни на что не влияет.
Если loopback недоступен (нет устройства, старая Windows), Рэм работает как раньше.
"""
from __future__ import annotations

import collections
import logging
import threading
import time

import numpy as np

log = logging.getLogger("rem.echo")

RATE = 16000
KEEP = 15.0                         # секунд в памяти


def to_mono_16k(data: bytes, channels: int, rate: int) -> np.ndarray:
    """int16 со смешанными каналами и любой частотой → моно 16 кГц."""
    a = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if channels > 1:
        a = a[: len(a) // channels * channels].reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(a):
        step = rate / RATE
        if step == int(step):                     # 48000 → 16000: среднее по тройкам
            k = int(step)
            a = a[: len(a) // k * k].reshape(-1, k).mean(axis=1)
        else:                                     # 44100 и прочее
            n = int(len(a) / step)
            a = np.interp(np.arange(n) * step, np.arange(len(a)), a)
    return np.clip(a, -32768, 32767).astype(np.int16)


class SpeakerTap:
    """Копия звука колонок за последние KEEP секунд."""

    def __init__(self):
        self.chunks: collections.deque[tuple[float, np.ndarray]] = collections.deque()
        self.lock = threading.Lock()
        self.available = False
        self.device_name = ""
        self._pa = None
        self._stream = None
        self._stop = threading.Event()

    # ——— запуск ———

    def start(self) -> bool:
        """True — слушаем колонки. Ошибки не роняют Рэм: просто без этой проверки."""
        self._stop = threading.Event()
        try:
            self._open()
        except Exception as e:
            log.warning("звук колонок недоступен: %s", e)
            self.available = False
            return False
        threading.Thread(target=self._follow_default, name="speakers", daemon=True).start()
        return True

    def _open(self) -> None:
        import pyaudiowpatch as pyaudio
        if self._pa is None:
            self._pa = pyaudio.PyAudio()
        dev = self._pa.get_default_wasapi_loopback()
        channels = int(dev["maxInputChannels"])
        rate = int(dev["defaultSampleRate"])

        def cb(data, frames, info, status):
            chunk = to_mono_16k(data, channels, rate)
            now = time.monotonic()
            with self.lock:
                self.chunks.append((now, chunk))
                while self.chunks and now - self.chunks[0][0] > KEEP:
                    self.chunks.popleft()
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                                     input_device_index=dev["index"], frames_per_buffer=rate // 10,
                                     stream_callback=cb)
        self.device_name = dev["name"]
        self.available = True
        log.info("слушаю колонки: %s", self.device_name)

    def _close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _follow_default(self) -> None:
        """Переключили вывод (наушники ↔ колонки) — переоткрываем на новом устройстве."""
        while not self._stop.wait(10):
            try:
                name = self._pa.get_default_wasapi_loopback()["name"]
            except Exception:
                continue
            if name != self.device_name:
                log.info("вывод звука сменился: %s", name)
                self._close()
                try:
                    self._open()
                except Exception as e:
                    log.warning("не открыл колонки заново: %s", e)
                    self.available = False

    def stop(self) -> None:
        self._stop.set()
        self._close()
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    # ——— чтение ———

    def recent(self, seconds: float) -> np.ndarray | None:
        """Звук колонок за последние seconds секунд. Когда ничего не играет, Windows
        данных не присылает — тогда None (или короче, чем просили)."""
        since = time.monotonic() - seconds
        with self.lock:
            parts = [c for t, c in self.chunks if t >= since]
        return np.concatenate(parts) if parts else None

    def feed(self, chunk: np.ndarray) -> None:
        """Подложить звук вручную — для проверок без колонок."""
        with self.lock:
            self.chunks.append((time.monotonic(), chunk))
