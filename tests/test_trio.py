"""Перебивание, выученные команды, диктовка — без микрофона и модели."""
import json

import numpy as np
import pytest

from rem import config as cfgmod
from rem.aec import EchoCanceller
from rem.assistant import Assistant
from rem.dictation import Dictation, split_stop
from rem.listen import is_stop
from rem.phrasebook import LIMIT, Phrasebook, learnable
from rem.skills import REGISTRY
from rem.skills.apps import AppIndex

from .test_core import Steps

SITE = {"actions": [{"skill": "open_site", "args": {"url": "music.yandex.ru"}}], "reply": ""}


def _assistant(client, monkeypatch, opened):
    monkeypatch.setattr(REGISTRY["open_site"], "handler", lambda ctx, url: opened.append(url))
    a = Assistant(dict(cfgmod.DEFAULTS), client=client, apps=AppIndex([]))
    a.say = lambda t: None
    return a


# ——— выученные команды ———

def test_model_answer_learned_then_used_without_model(monkeypatch):
    client, opened = Steps(SITE), []
    a = _assistant(client, monkeypatch, opened)
    assert a.handle("включи что-нибудь спокойное").source == "llm"
    plan = a.handle("Включи что-нибудь спокойное.")
    assert plan.source == "learned" and client.calls == 1 and opened == ["music.yandex.ru"] * 2
    # и после перезапуска
    b = _assistant(client, monkeypatch, opened)
    assert b.handle("включи что-нибудь спокойное").source == "learned" and client.calls == 1


def test_not_that_forgets_last_learned(monkeypatch):
    client, opened = Steps(SITE), []
    a = _assistant(client, monkeypatch, opened)
    said = []
    a.say = said.append
    a.handle("включи что-нибудь спокойное")
    a.handle("Не то!")
    assert "забыла" in said[-1] and len(a.book) == 0
    a.handle("включи что-нибудь спокойное")
    assert client.calls == 2                     # снова спросили модель
    a.handle("громкость 30")                     # быстрый путь — забывать нечего
    a.handle("не то")
    assert said[-1].startswith("Поняла. Скажите по-другому")


def test_forget_all(monkeypatch):
    a = _assistant(Steps(SITE), monkeypatch, [])
    a.handle("включи что-нибудь спокойное")
    said = []
    a.say = said.append
    a.handle("Забудь выученные команды.")
    assert len(a.book) == 0 and said == ["Забыла выученные команды: 1."]


def test_not_learned_when_context_or_confirm_or_fail(monkeypatch):
    client, opened = Steps(SITE), []
    a = _assistant(client, monkeypatch, opened)
    a.handle("включи его ещё раз")                # «его», «ещё» — смысл из прошлого разговора
    assert len(a.book) == 0
    skills = a.by_name
    assert not learnable("выключи компьютер", [("shutdown_pc", {})], skills)        # спрашивает «да»
    assert not learnable("напиши привет", [("type_text", {"text": "привет"})], skills)  # что на экране
    assert not learnable("найди отчёт", [("find_files", {"query": "отчёт"})], skills)   # пересказ данных
    assert learnable("открой музыку", [("open_site", {"url": "x"})], skills)
    monkeypatch.setattr(REGISTRY["open_site"], "handler", lambda ctx, url: 1 / 0)
    a.reload()
    a.handle("открой музыку для работы")
    assert len(a.book) == 0


def test_learned_that_stopped_working_goes_back_to_model(monkeypatch):
    from rem.skills.base import NotHere
    client, opened = Steps(SITE), []
    a = _assistant(client, monkeypatch, opened)
    a.book.learn("включи что-нибудь спокойное", [("switch_window", {"name": "плеер"})])

    def gone(ctx, name):
        raise NotHere("нет такого окна")
    monkeypatch.setattr(REGISTRY["switch_window"], "handler", gone)
    a.reload()
    plan = a.handle("включи что-нибудь спокойное")
    assert plan.source == "llm" and opened == ["music.yandex.ru"]
    assert a.book.get("включи что-нибудь спокойное") == [("open_site", {"url": "music.yandex.ru"})]


def test_learning_off(monkeypatch):
    client = Steps(SITE)
    a = _assistant(client, monkeypatch, [])
    a.config["learn_phrases"] = False
    a.handle("включи что-нибудь спокойное")
    a.handle("включи что-нибудь спокойное")
    assert client.calls == 2 and len(a.book) == 0


def test_phrasebook_limit_and_broken_file(tmp_path):
    p = tmp_path / "phrasebook.json"
    p.write_text("{битый", encoding="utf-8")
    book = Phrasebook(p)
    assert len(book) == 0
    for i in range(LIMIT + 5):
        book.items[f"фраза {i}"] = {"actions": [["tell_time", {}]], "used": f"2026-01-01T00:00:{i:05d}"}
    book.learn("новая фраза", [("tell_time", {})])
    assert len(book) == LIMIT and "новая фраза" in book.items and "фраза 0" not in book.items
    assert json.loads(p.read_text(encoding="utf-8"))["новая фраза"]["said"] == "новая фраза"


# ——— диктовка ———

def test_dictation_flow():
    out = []
    d = Dictation(out.append, lambda: out.append("⏎"), lambda n: out.append(f"⌫{n}"))
    assert d.feed("Купить хлеба.")
    assert d.feed("И молока.")
    assert d.feed("Сотри последнее.")
    assert d.feed("С новой строки.")
    assert d.feed("Позвонить маме.")
    assert d.feed("")
    assert not d.feed("Всё на сегодня. Хватит.")
    assert out == ["Купить хлеба.", " И молока.", "⌫10", "⏎", "Позвонить маме.", " Всё на сегодня."]


def test_dictation_stop_word_inside_text_is_typed():
    assert split_stop("Хватит это терпеть.") == ("Хватит это терпеть.", False)
    assert split_stop("Хватит.") == ("", True)
    assert split_stop("Конец диктовки!") == ("", True)


def test_dictation_only_fast_path(monkeypatch):
    from rem.brain import build_system_prompt
    a = _assistant(Steps(SITE), monkeypatch, [])
    assert a.fast.match("Записывай.").actions == [("dictation", {})]
    assert "dictation" not in build_system_prompt(a.brain.skills)
    said = []
    a.say = said.append
    a.handle("записывай")                         # без микрофона — честно говорит, что не может
    assert said == ["Диктовка работает только с микрофоном."]


# ——— перебивание ———

@pytest.mark.parametrize("text,stop", [("Хватит.", True), ("стоп", True), ("Всё, спасибо!", True),
                                       ("Открой браузер.", False), ("", False), ("хватит громкость", False)])
def test_is_stop(text, stop):
    assert is_stop(text) is stop


def test_echo_canceller_removes_own_voice_and_warms_up():
    ec = EchoCanceller()
    if not ec.ok:
        pytest.skip("нет пакета livekit")
    import time
    rng = np.random.default_rng(1)
    t0 = time.monotonic() + 1.0
    voice = (rng.standard_normal(16000 * 5) * 3000).astype(np.int16)
    ec.played(voice, t0)
    assert ec.active() and not ec.ready
    echo = np.concatenate([np.zeros(1600), voice[:-1600] * 0.5])        # 100 мс в комнате
    out = [ec.process(echo[i:i + 1600].astype(np.int16), t0 + i / 16000) for i in range(0, len(echo), 1600)]
    tail = np.concatenate(out[-10:]).astype(np.float64)
    assert ec.ready
    assert np.sqrt(np.mean(tail ** 2)) < np.sqrt(np.mean(echo[-16000:] ** 2)) / 10     # тише в 10 с лишним раз
    ec.silence_from(t0 + 2.0)
    assert not any(k > int((t0 + 2.0) * 16000) // 160 + 1 for k in ec._ref)


def test_voice_stop_drops_queue_and_remembers_recent():
    from rem.speech import Voice
    v = Voice(config={"speak_replies": True})
    v.ready.wait(5)
    v.q.put(("say", "раз", None))
    v.q.put(("config", {"voice_engine": "windows", "x": 1}))
    v.q.put(("say", "два", None))
    v.stop()
    v.wait()
    assert v.cfg.get("x") == 1                     # настройки из очереди не потерялись
    v.say("Рэм всё сделала.")
    v.wait()
    assert "Рэм всё сделала." in v.recent(5)


def test_wait_voice_runs_barge_command(monkeypatch):
    client, opened = Steps(SITE), []
    a = _assistant(client, monkeypatch, opened)

    class Voice:
        def wait(self):
            pass

    class Listener:
        heard = ["Открой музыку."]

        def can_barge(self):
            return True

        def wait_speaking(self):
            return self.heard.pop(0) if self.heard else None

    a.voice, a.listener = Voice(), Listener()
    a.handle("включи что-нибудь спокойное")       # ответ перебили: «Рэм, открой музыку»
    assert client.calls == 2 and opened == ["music.yandex.ru"] * 2
