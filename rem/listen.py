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

# Входы, которые на самом деле не микрофон, а звук самого компьютера
NOT_MIC = ("stereo mix", "стерео микшер", "what u hear", "wave out", "loopback", "cable output")


def is_real_mic(name: str) -> bool:
    n = name.lower()
    return not any(x in n for x in NOT_MIC)


def input_devices() -> list[tuple[int, str]]:
    """Входы (индекс, имя) в системном API по умолчанию. Каждый вход Windows виден
    по нескольку раз (MME, WASAPI…) — берём один набор. «Переназначение звука» — не вход."""
    import sounddevice as sd
    api = sd.default.hostapi
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0 and d["hostapi"] == api
            and not d["name"].lower().startswith(("microsoft sound mapper", "переназначение", "первичный драйвер"))]


def pick_input(wanted: str | None) -> int | None:
    """Номер устройства для записи. wanted — имя из настроек (или его начало);
    пусто — вход Windows по умолчанию, если это настоящий микрофон, иначе первый микрофон."""
    import sounddevice as sd
    inputs = input_devices()
    if wanted:
        for i, name in inputs:
            if name == wanted or name.startswith(wanted) or wanted.startswith(name):
                return i
        log.warning("микрофон «%s» не найден — беру по умолчанию", wanted)
    try:
        default = sd.query_devices(kind="input")
    except Exception:
        default = None
    if default and is_real_mic(default["name"]):
        return None                              # пусть Windows решает: переживёт смену устройства
    if default:
        log.warning("вход по умолчанию «%s» — это не микрофон", default["name"])
    mics = [i for i, name in inputs if is_real_mic(name)]
    return mics[0] if mics else None


class Microphone:
    """Поток с микрофона кусками по 100 мс, 16 кГц, моно, int16.
    last_t — когда записано начало последнего прочитанного куска (time.monotonic):
    по нему эхоподавитель сопоставляет микрофон с голосом Рэм."""

    def __init__(self, device=None):
        self.device = device
        self.q: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(maxsize=600)   # не больше минуты
        self.stream = None
        self.last_t = 0.0

    def _cb(self, data, frames, t, status):
        ago = frames / RATE                         # кусок кончился только что
        try:
            if t.currentTime > 0 and 0 < t.currentTime - t.inputBufferAdcTime < 1:
                ago = t.currentTime - t.inputBufferAdcTime    # точнее: когда записан первый отсчёт
        except Exception:
            pass
        try:
            self.q.put_nowait((time.monotonic() - ago, np.frombuffer(data, dtype=np.int16).copy()))
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
            self.last_t, chunk = self.q.get(timeout=timeout)
            return chunk
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


# ——— звук из колонок ———

def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio.astype(np.float32) ** 2))) if len(audio) else 0.0


def wake_from_speakers(asr: ASR, speaker_audio: np.ndarray | None, wake: str, is_word=lambda w: False) -> bool:
    """Звучало ли слово активации из колонок в то же время, что и в микрофоне.

    Если да — микрофон услышал фильм или видео («Рем» в аниме), а не человека.
    Тихие колонки не проверяем: распознавание стоит ~0,4 с.
    """
    if speaker_audio is None or rms(speaker_audio) < 100:
        return False
    text = asr.recognize(speaker_audio)
    # в фильме слово может стоять где угодно: «…и тут Рем пришла»
    return find_wake(text, wake, is_word)[0] or any(_norm(w) == _norm(wake) for w in text.split())


# ——— определение речи по громкости ———

class NoiseFloor:
    """Уровень фонового шума комнаты: 20-й перцентиль громкости за последние 5 секунд.
    Речь — когда кусок заметно громче фона. Обновляется постоянно, поэтому
    подстраивается под вентилятор, музыку тихо фоном и т. п."""

    def __init__(self, window: int = 50):
        self.levels: collections.deque[float] = collections.deque(maxlen=window)

    @staticmethod
    def rms(chunk: np.ndarray) -> float:
        return float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

    def update(self, chunk: np.ndarray) -> float:
        level = self.rms(chunk)
        self.levels.append(level)
        return level

    @property
    def floor(self) -> float:
        return float(np.percentile(self.levels, 20)) if self.levels else 0.0

    def is_speech(self, chunk: np.ndarray) -> bool:
        level = self.update(chunk)
        return level > self.floor * 2.5 + 150


def record_phrase(mic: Microphone, noise: NoiseFloor | None = None, max_wait: float = 5.0,
                  max_len: float = 10.0, silence: float = 0.8,
                  already_speaking: bool = False, clean=None) -> np.ndarray | None:
    """Пишет речь до паузы в silence секунд.

    already_speaking=False — сначала ждёт начала речи до max_wait секунд;
    already_speaking=True — человек уже говорит (дослушиваем фразу после сторожа).
    clean — очистка куска (эхоподавление).
    """
    noise = noise or NoiseFloor()
    started, quiet, chunks = already_speaking, 0.0, []
    t0 = time.monotonic()
    while True:
        c = mic.read(timeout=0.5)
        if c is not None and clean is not None:
            c = clean(c)
        if c is None:
            if (not started and time.monotonic() - t0 > max_wait) or (started and quiet >= silence):
                break
            quiet += 0.5 if started else 0
            continue
        if noise.is_speech(c):
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


# «Рэм, хватит» поверх ответа — просто замолчать
STOP_WORDS = {"стоп", "хватит", "замолчи", "молчи", "помолчи", "тихо", "тише", "достаточно",
              "все", "понятно", "ясно", "спасибо", "отмена", "не надо"}


def is_stop(text: str) -> bool:
    t = " ".join(re.sub(r"[^а-яё ]", " ", text.lower()).replace("ё", "е").split())
    return t in STOP_WORDS or (bool(t) and all(w in STOP_WORDS for w in t.split()))


class Listener:
    """Склеивает всё вместе. listen() блокирует поток и вызывает колбэки."""

    def __init__(self, vosk_model, asr: ASR, wake: str, mic: Microphone,
                 speakers=None, mute: threading.Event | None = None, echo=None, voice=None):
        self.vosk_model = vosk_model
        self.asr = asr
        self.mic = mic
        self.speakers = speakers                # echo.SpeakerTap — что играет в колонках
        self.mute = mute                        # пока Рэм говорит сама — без эхоподавления не слушаем
        self.echo = echo                        # aec.EchoCanceller — слышать человека поверх голоса Рэм
        self.voice = voice                      # speech.Voice — замолчать, когда перебили
        self.barge_in = True                    # настройка «перебивать голосом»
        self.barge_partial = True               # замолкать по промежуточному результату сторожа
        self.paused = threading.Event()
        self.stopped = threading.Event()
        self.noise = NoiseFloor()
        self.stats = {"guard": 0, "accepted": 0, "rejected": 0, "speakers": 0, "barge": 0}
        self.set_wake(wake)

    def set_wake(self, wake: str) -> None:
        self.wake = wake.lower().strip()
        self.guard = WakeGuard(self.vosk_model, self.wake)
        self.bguard = WakeGuard(self.vosk_model, self.wake)      # сторож поверх голоса Рэм

    # ——— перебивание ———

    def _clean(self, chunk: np.ndarray) -> np.ndarray:
        """Кусок микрофона без голоса Рэм (если она говорит и есть эхоподавитель)."""
        if self.echo is None or not self.echo.ok:
            return chunk
        out = self.echo.process(chunk, self.mic.last_t)          # непрерывно: следит за комнатой
        return out if self.echo.active() else chunk

    def can_barge(self) -> bool:
        return bool(self.barge_in and self.echo is not None and self.echo.ready and self.voice is not None)

    def _barge_feed(self, chunk: np.ndarray) -> str | None:
        """Рэм говорит, chunk уже очищен от её голоса. Человек сказал «Рэм» → она замолкает
        и дослушивает уже в тишине. → текст команды («» — только имя) или None.

        Замер: сторож по промежуточному результату срабатывает через ~0,2 с после «Рэм»,
        поэтому команда звучит уже без её голоса. GigaAM слово «Рэм», сказанное поверх голоса,
        часто не узнаёт — тогда команда берётся из того, что сказано после остановки."""
        if self.echo is None or not self.echo.ready:
            return None                         # эхоподавитель ещё не подстроился под комнату
        g = self.bguard
        g.buf.append(chunk)
        g.buf_len += len(chunk)
        while g.buf_len > g.MAX_UTTERANCE:
            g.buf_len -= len(g.buf.popleft())
        final = g.rec.AcceptWaveform(chunk.tobytes())
        if not final and not self.barge_partial:
            return None
        words = (json.loads(g.rec.Result()).get("text", "") if final
                 else json.loads(g.rec.PartialResult()).get("partial", "")).split()
        if self.wake not in words:
            if final:
                g.buf.clear()
                g.buf_len = 0
            return None
        audio = np.concatenate(g.buf)
        own = self.voice.recent(2.0)            # что она говорила, пока звучало слово (сторож отстаёт ≤ 2 с)
        says_wake = _norm(self.wake) in {_norm(w) for w in own.split()}
        if says_wake and (not final or self.speakers is None):
            return None                         # «Рэм всё сделала» — сама произносит имя: не верим
        if says_wake:                           # …пока колонки не покажут, что это не она
            heard = self.speakers.recent(len(audio) / RATE + 0.5)
            if wake_from_speakers(self.asr, heard, self.wake, self.is_word):
                log.info("перебивание: «Рэм» из колонок — это она сама")
                g.reset()
                return None
        self.voice.stop()
        self.stats["barge"] += 1
        g.reset()
        time.sleep(0.15)                        # хвост её голоса в комнате
        tail = record_phrase(self.mic, self.noise, max_wait=2.0, max_len=8, silence=0.7,
                             already_speaking=not final, clean=self._clean)
        whole = np.concatenate([audio, tail]) if tail is not None else audio
        text = self.asr.recognize(whole)
        ok, command = find_wake(text, self.wake, self.is_word)
        if not ok:
            command = self.asr.recognize(tail) if tail is not None else ""
        if not command.strip(" .,!?"):          # «Рэм, …» и пауза — ждём саму команду
            more = record_phrase(self.mic, self.noise, max_wait=1.5, max_len=8, silence=0.7, clean=self._clean)
            command = self.asr.recognize(more) if more is not None else ""
        log.info("перебили: «%s» → «%s»", text, command)
        return command

    def wait_speaking(self) -> str | None:
        """Пока Рэм говорит — слушать, не перебьют ли. → команда (как _barge_feed) или None."""
        self.mic.flush()                        # то, что накопилось, пока она думала
        self.bguard.reset()
        raw = cleaned = 0.0
        try:
            while self.voice.speaking.is_set() and not self.stopped.is_set():
                chunk = self.mic.read(timeout=0.3)
                if chunk is None or self.paused.is_set():
                    continue
                clean = self._clean(chunk)
                raw += float(np.sum(chunk.astype(np.float64) ** 2))
                cleaned += float(np.sum(clean.astype(np.float64) ** 2))
                self.noise.update(clean)
                got = self._barge_feed(clean)
                if got is not None:
                    return got
            return None
        finally:
            if raw > 0 and cleaned > 0:         # для отчёта с компьютера: работает ли эхоподавление
                log.info("эхо её голоса ослаблено на %.0f дБ", 10 * np.log10(raw / cleaned))

    def is_word(self, w: str) -> bool:
        return self.vosk_model.vosk_model_find_word(w) >= 0

    def listen(self, on_command, on_wake_only, on_rejected=None, on_stop=None) -> None:
        deaf_until = 0.0
        speaking = False
        while not self.stopped.is_set():
            chunk = self.mic.read(timeout=0.5)
            if chunk is None:
                continue
            if self.paused.is_set():
                continue
            chunk = self._clean(chunk)
            if self.mute is not None and self.mute.is_set():
                if self.can_barge():             # Рэм говорит сама (таймер) — но её можно перебить
                    if not speaking:
                        self.bguard.reset()
                        speaking = True
                    got = self._barge_feed(chunk)
                    if got is not None:
                        speaking = False
                        self._dispatch(got, on_command, on_wake_only, on_stop)
                    continue
                deaf_until = time.monotonic() + 0.4      # и хвост эха после фразы
            speaking = False
            if time.monotonic() < deaf_until:
                self.guard.reset()
                continue
            self.noise.update(chunk)
            audio = self.guard.feed(chunk)
            if audio is None:
                continue
            self.stats["guard"] += 1
            # сторож мог закончить фразу на паузе после «Рэм,» — дослушиваем до конца речи
            tail = record_phrase(self.mic, self.noise, max_len=8, silence=0.7, already_speaking=True)
            if tail is not None:
                audio = np.concatenate([audio, tail])
            t = time.perf_counter()
            text = self.asr.recognize(audio)
            ok, command = find_wake(text, self.wake, self.is_word)
            log.info("сторож: «%s» → %s (%.2f с)", text, "да" if ok else "нет", time.perf_counter() - t)
            if ok and self.speakers is not None:
                heard = self.speakers.recent(len(audio) / RATE + 0.5)
                if wake_from_speakers(self.asr, heard, self.wake, self.is_word):
                    log.info("слово прозвучало из колонок — не реагирую")
                    self.stats["speakers"] += 1
                    ok = False
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

    def _dispatch(self, command: str, on_command, on_wake_only, on_stop=None) -> None:
        if is_stop(command):
            if on_stop:
                on_stop()
        elif command:
            on_command(command)
        else:
            on_wake_only()
        self.guard.reset()
        self.mic.flush()

    def hear_command(self) -> str:
        """После «голого» слова активации: записать и распознать команду."""
        audio = record_phrase(self.mic, self.noise)
        return self.asr.recognize(audio) if audio is not None else ""

    def hear_yes_no(self, timeout: float = 5.0) -> bool:
        """Подтверждение: True только на явное «да»."""
        audio = record_phrase(self.mic, self.noise, max_wait=timeout, max_len=3)
        if audio is None:
            return False
        words = {_norm(w) for w in self.asr.recognize(audio).split()}
        return bool(words & {"да", "давай", "подтверждаю", "конечно", "ага"}) and "нет" not in words
