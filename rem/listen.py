"""Слух: микрофон → сторож слова активации → проверка → текст команды.

Двухступенчатая активация (проверено замерами):
  1. Сторож — Vosk, настроенный только на слово активации. Почти ничего не стоит
     (~5% одного ядра) и не пропускает слово, но срабатывает и на похожие («тремя»).
  2. Проверка — GigaAM распознаёт всю фразу целиком. Помощник реагирует, только
     если слово активации стоит в начале фразы отдельным словом.
GigaAM же распознаёт и саму команду: «Рэм, открой браузер» — одна фраза, одна проверка.
"""
from __future__ import annotations

import collections
import json
import logging
import queue
import re
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("rem.listen")

RATE = 16000
CHUNK = 1600                      # 100 мс


# ——— проверка слова активации ———

_LAT = str.maketrans({"a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
                      "i": "и", "j": "й", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
                      "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
                      "y": "ы", "z": "з"})


def _norm(w: str) -> str:
    """Для сравнения: кириллица, без дублей. Латиница переводится — GigaAM иногда
    пишет слово вперемешку: «Rэm»."""
    w = re.sub(r"[^a-zа-яё]", "", w.lower()).translate(_LAT).replace("ё", "е").replace("э", "е")
    return re.sub(r"(.)\1+", r"\1", w)          # «ремм», «ррэм» → «рем»


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


FILLERS = {_norm(w) for w in ("эй", "и", "ну", "слушай", "а", "э", "ээ", "алло")}


def find_wake(text: str, wake: str, is_word=lambda w: False) -> tuple[bool, str]:
    """Есть ли обращение к помощнику → (да/нет, текст команды).

    Засчитывается:
      * слово активации в начале любого предложения: «Сколько времени? Рэм, сделай громче»;
      * обращение в конце через запятую: «Открой браузер, Рэм»;
      * слово отдельным предложением в конце: «…Рэм.» — команда прозвучит следом.
    Близкое написание («Ремм», «Рям») засчитывается, только если это не настоящее слово:
    иначе «крем» или «рам» будили бы помощника.
    """
    target = _norm(wake)

    def is_wake(raw: str) -> bool:
        w = _norm(raw)
        bare = raw.lower().strip(",.!?:;-—")
        return bool(w) and (w == target or (len(w) >= 3 and _lev(w, target) <= 1 and not is_word(bare)))

    sentences = [x for x in re.split(r"(?<=[.!?…])\s+", text.strip()) if x]
    for si, sent in enumerate(sentences):
        words = sent.split()
        for i, raw in enumerate(words[:3]):
            if is_wake(raw):
                rest = " ".join(words[i + 1:] + sentences[si + 1:])
                return True, rest.strip(" ,.!?-—")
            if _norm(raw) not in FILLERS:
                break
    if sentences:
        words = sentences[-1].split()
        # «Открой браузер, Рэм» — обращение через запятую в конце
        if len(words) >= 2 and is_wake(words[-1]) and words[-2].endswith(","):
            return True, " ".join(words[:-1]).strip(" ,.!?-—")
    return False, ""


# ——— микрофон ———

class Microphone:
    """Поток с микрофона кусками по 100 мс, 16 кГц, моно, int16."""

    def __init__(self, device=None):
        self.device = device
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=600)   # не больше минуты
        self.stream = None

    def _cb(self, data, frames, t, status):
        try:
            self.q.put_nowait(np.frombuffer(data, dtype=np.int16).copy())
        except queue.Full:
            pass

    def start(self) -> None:
        import sounddevice as sd
        self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                        blocksize=CHUNK, device=self.device, callback=self._cb)
        self.stream.start()

    def stop(self) -> None:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self) -> None:
        """Выбросить накопленное — например, собственный голос помощника."""
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break


# ——— сторож ———

class WakeGuard:
    """Ступень 1. feed() возвращает аудио фразы, в которой сторож услышал слово."""

    MAX_UTTERANCE = 10 * RATE

    def __init__(self, vosk_model, wake: str):
        import vosk
        self.rec = vosk.KaldiRecognizer(vosk_model, RATE, json.dumps([wake, "[unk]"], ensure_ascii=False))
        self.wake = wake
        self.buf: collections.deque[np.ndarray] = collections.deque()
        self.buf_len = 0

    def reset(self) -> None:
        self.rec.Reset()
        self.buf.clear()
        self.buf_len = 0

    def feed(self, chunk: np.ndarray) -> np.ndarray | None:
        self.buf.append(chunk)
        self.buf_len += len(chunk)
        while self.buf_len > self.MAX_UTTERANCE:
            self.buf_len -= len(self.buf.popleft())
        if not self.rec.AcceptWaveform(chunk.tobytes()):
            return None
        heard = self.wake in json.loads(self.rec.Result()).get("text", "").split()
        audio = np.concatenate(self.buf) if heard else None
        self.buf.clear()
        self.buf_len = 0
        return audio


# ——— распознавание речи ———

class ASR:
    """GigaAM v3 (с пунктуацией, числа цифрами) через onnxruntime, на процессоре."""

    def __init__(self, model_dir: Path, threads: int = 3):
        import onnx_asr
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, threads)
        so.inter_op_num_threads = 1
        self.model = onnx_asr.load_model("gigaam-v3-e2e-ctc", str(model_dir), quantization="int8",
                                         sess_options=so, providers=["CPUExecutionProvider"])

    def recognize(self, audio: np.ndarray) -> str:
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768
        return self.model.recognize(audio, sample_rate=RATE).strip()


# ——— запись речи с определением конца фразы ———

def record_phrase(mic: Microphone, max_wait: float = 5.0, max_len: float = 10.0,
                  silence: float = 0.8, already_speaking: bool = False) -> np.ndarray | None:
    """Пишет речь до паузы в silence секунд.

    already_speaking=False — сначала ждёт начала речи до max_wait секунд;
    already_speaking=True — человек уже говорит (дослушиваем фразу после сторожа).
    """
    import webrtcvad
    vad = webrtcvad.Vad(2)
    frame = 480                                    # 30 мс
    started, quiet, chunks = already_speaking, 0.0, []
    t0 = time.monotonic()
    while True:
        c = mic.read(timeout=0.5)
        if c is None:
            if (not started and time.monotonic() - t0 > max_wait) or (started and quiet >= silence):
                break
            quiet += 0.5 if started else 0
            continue
        voiced = sum(vad.is_speech(c[i:i + frame].tobytes(), RATE)
                     for i in range(0, len(c) - frame + 1, frame))
        if voiced >= 2:
            started, quiet = True, 0.0
        elif started:
            quiet += len(c) / RATE
        if started:
            chunks.append(c)
            if quiet >= silence or sum(map(len, chunks)) >= max_len * RATE:
                break
        elif time.monotonic() - t0 > max_wait:
            break
    return np.concatenate(chunks) if chunks else None


class Listener:
    """Склеивает всё вместе. listen() блокирует поток и вызывает колбэки."""

    def __init__(self, vosk_model, asr: ASR, wake: str, mic: Microphone):
        self.vosk_model = vosk_model
        self.asr = asr
        self.mic = mic
        self.paused = threading.Event()
        self.stopped = threading.Event()
        self.stats = {"guard": 0, "accepted": 0, "rejected": 0}
        self.set_wake(wake)

    def set_wake(self, wake: str) -> None:
        self.wake = wake.lower().strip()
        self.guard = WakeGuard(self.vosk_model, self.wake)

    def is_word(self, w: str) -> bool:
        return self.vosk_model.vosk_model_find_word(w) >= 0

    def listen(self, on_command, on_wake_only, on_rejected=None) -> None:
        while not self.stopped.is_set():
            chunk = self.mic.read(timeout=0.5)
            if chunk is None:
                continue
            if self.paused.is_set():
                continue
            audio = self.guard.feed(chunk)
            if audio is None:
                continue
            self.stats["guard"] += 1
            # сторож мог закончить фразу на паузе после «Рэм,» — дослушиваем до конца речи
            tail = record_phrase(self.mic, max_len=8, silence=0.7, already_speaking=True)
            if tail is not None:
                audio = np.concatenate([audio, tail])
            t = time.perf_counter()
            text = self.asr.recognize(audio)
            ok, command = find_wake(text, self.wake, self.is_word)
            log.info("сторож: «%s» → %s (%.2f с)", text, "да" if ok else "нет", time.perf_counter() - t)
            if not ok:
                self.stats["rejected"] += 1
                if on_rejected:
                    on_rejected(text)
                continue
            self.stats["accepted"] += 1
            if command:
                on_command(command)
            else:
                on_wake_only()
            self.guard.reset()
            self.mic.flush()

    def hear_command(self) -> str:
        """После «голого» слова активации: записать и распознать команду."""
        audio = record_phrase(self.mic)
        return self.asr.recognize(audio) if audio is not None else ""

    def hear_yes_no(self, timeout: float = 5.0) -> bool:
        """Подтверждение: True только на явное «да»."""
        audio = record_phrase(self.mic, max_wait=timeout, max_len=3)
        if audio is None:
            return False
        words = {_norm(w) for w in self.asr.recognize(audio).split()}
        return bool(words & {"да", "давай", "подтверждаю", "конечно", "ага"}) and "нет" not in words
