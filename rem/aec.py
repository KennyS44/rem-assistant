"""Эхоподавление: Рэм слышит человека поверх собственного голоса — её можно перебить.

Всё, что Рэм говорит, проходит через проигрыватель (speech.play_wav), и он отдаёт сюда копию
звука с отметкой времени. Перед сторожем из микрофона вычитается этот звук — эхоподавитель
WebRTC AEC3 (тот же, что в браузерах; пакет livekit, Apache 2.0). Задержку между колонками
и микрофоном он находит сам — нужно лишь, чтобы копия приходила не позже эха и не слишком
рано (LEAD_S).

Замер (голос Рэм из колонок, поверх него «Рэм, открой браузер», 12 вариантов громкости
и задержки): без эхоподавления сторож 5–11 раз за 84 с будил Рэм её собственным голосом
(в её фразах есть «Рэм»), с ним — ни разу; «Рэм» человека — 7–9 из 12.

Нет пакета или он не загрузился — Рэм работает как раньше: пока говорит, не слушает.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

log = logging.getLogger("rem.aec")

RATE = 16000
BLOCK = RATE // 100                 # 10 мс — кадр эхоподавителя
KEEP_BLOCKS = 300                   # копия звука хранится 3 с
TAIL_S = 0.6                        # после конца фразы ещё звучит эхо комнаты
LEAD_S = 0.15                       # копия должна опережать эхо: замер — гасит 38–43 дБ при опережении
                                    # 0,1–0,3 с, ~20 дБ при 0,4–0,5 с, ничего при 0,6 с и при опоздании
WARMUP_S = 3.0                      # первые секунды голоса эхоподавитель подстраивается под комнату


class EchoCanceller:
    def __init__(self):
        self.ok = False
        self._ref: dict[int, np.ndarray] = {}
        self._lock = threading.Lock()
        self._until = 0.0           # до какого момента звучит Рэм (по часам monotonic)
        self.learned_s = 0.0        # сколько голоса Рэм он уже слышал
        try:
            from livekit import rtc
            self._rtc = rtc
            self._apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=False,
                                                  high_pass_filter=True, auto_gain_control=False)
            self._zero = np.zeros(BLOCK, dtype=np.int16)
            self.ok = True
        except Exception as e:      # нет пакета, не загрузилась библиотека — без перебивания
            log.warning("эхоподавление недоступно: %s", e)

    # ——— что звучит из колонок (поток проигрывателя) ———

    def played(self, samples: np.ndarray, t0: float) -> None:
        """samples — int16, 16 кГц, моно; t0 — когда начинается их звучание (time.monotonic)."""
        if not self.ok or not len(samples):
            return
        start = int(round(t0 * RATE))
        lead = start % BLOCK
        a = np.concatenate([np.zeros(lead, dtype=np.int32), samples.astype(np.int32)])
        a = np.concatenate([a, np.zeros(-len(a) % BLOCK, dtype=np.int32)]).reshape(-1, BLOCK)
        first = start // BLOCK
        with self._lock:
            for i, blk in enumerate(a):
                cur = self._ref.get(first + i)
                self._ref[first + i] = blk if cur is None else cur + blk     # две фразы сразу — сумма
            self._until = max(self._until, t0 + len(samples) / RATE)
            old = int(time.monotonic() * RATE) // BLOCK - KEEP_BLOCKS     # микрофон это время уже прошёл
            if self._ref and min(self._ref) < old:
                for k in [k for k in self._ref if k < old]:
                    del self._ref[k]

    def silence_from(self, t: float) -> None:
        """Рэм замолчала на полуслове: то, что ещё не прозвучало, из копии убираем."""
        first = int(round(t * RATE)) // BLOCK + 1
        with self._lock:
            for k in [k for k in self._ref if k > first]:
                del self._ref[k]
            self._until = min(self._until, t)

    def active(self, now: float | None = None) -> bool:
        """Рэм говорит (или только что замолчала) — микрофон надо очищать."""
        return self.ok and (now or time.monotonic()) < self._until + TAIL_S

    @property
    def ready(self) -> bool:
        """Подстроился под комнату: в замере до этого бывали ложные «Рэм» из её же голоса."""
        return self.ok and self.learned_s >= WARMUP_S

    # ——— микрофон (поток слуха) ———

    def process(self, chunk: np.ndarray, t0: float) -> np.ndarray:
        """Кусок микрофона (int16, 16 кГц), записанный начиная с t0 → он же без голоса Рэм.
        Вызывать для каждого куска подряд: эхоподавитель следит за комнатой непрерывно."""
        if not self.ok:
            return chunk
        rtc = self._rtc
        first = int(round(t0 * RATE)) // BLOCK
        out = np.empty_like(chunk)
        n = len(chunk) // BLOCK
        for i in range(n):
            with self._lock:
                ref = self._ref.pop(first + i, None)
            if ref is None:
                ref = self._zero
            else:
                ref = np.clip(ref, -32768, 32767).astype(np.int16)
                self.learned_s += BLOCK / RATE
            self._apm.process_reverse_stream(rtc.AudioFrame(ref.tobytes(), RATE, 1, BLOCK))
            frame = rtc.AudioFrame(chunk[i * BLOCK:(i + 1) * BLOCK].tobytes(), RATE, 1, BLOCK)
            self._apm.process_stream(frame)
            out[i * BLOCK:(i + 1) * BLOCK] = np.frombuffer(bytes(frame.data), dtype=np.int16)
        out[n * BLOCK:] = chunk[n * BLOCK:]
        return out


def self_check() -> float:
    """Проверка для самопроверки сборки: на сколько дБ гасится эхо синтетического голоса.
    Без колонок и микрофона — только сама библиотека."""
    ec = EchoCanceller()
    if not ec.ok:
        return 0.0
    rng = np.random.default_rng(0)
    t = np.arange(RATE * 4) / RATE
    voice = (np.sin(2 * np.pi * 220 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) * 6000
             + rng.standard_normal(len(t)) * 1500)
    delay = int(0.05 * RATE)
    echo = np.concatenate([np.zeros(delay), voice[:-delay]]) * 0.6
    base = time.monotonic() + 1.0
    ec.played(voice.astype(np.int16), base)
    out = np.concatenate([ec.process(echo[i:i + 1600].astype(np.int16), base + i / RATE)
                          for i in range(0, len(echo), 1600)])
    last = slice(RATE * 3, RATE * 4)                # последняя секунда — фильтр уже подстроился
    before = np.sqrt(np.mean(echo[last] ** 2))
    after = np.sqrt(np.mean(out[last].astype(np.float64) ** 2)) + 1e-9
    return float(20 * np.log10(before / after))
