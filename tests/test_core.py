import datetime as dt
import json
import time
from pathlib import Path

import pytest

from rem import config as cfgmod
from rem.assistant import Assistant
from rem.brain import Plan, build_schema, validate
from rem.listen import find_wake
from rem.skills import active_skills
from rem.skills.apps import AppIndex
from rem.skills.custom import build_custom, parse_keys
from rem.skills.system import tell_date, tell_time
from rem.skills.web import site_url
from rem.text import duration_ru, elapsed_ru, plural, translit
from rem.timers import Timers

SKILLS = active_skills(cfgmod.DEFAULTS)


# ——— слово активации ———

@pytest.mark.parametrize("text,expected", [
    ("Рэм, открой браузер.", (True, "открой браузер")),
    ("Рем, сделай громче", (True, "сделай громче")),
    ("Эй, Рэм, включи музыку.", (True, "включи музыку")),
    ("Ремм, поставь таймер на 5 минут.", (True, "поставь таймер на 5 минут")),
    ("Рэм.", (True, "")),
    ("Сколько сейчас времени? Рэм, сделай громче.", (True, "сделай громче")),
    ("Сколько сейчас времени? Рэм.", (True, "")),       # команда прозвучит следом
    ("Открой браузер, Рэм.", (True, "Открой браузер")),
])
def test_wake_found(text, expected):
    assert find_wake(text, "рэм") == expected


@pytest.mark.parametrize("text", [
    "Мы пошли с тремя друзьями", "Сколько времени", "Теорема Пифагора", "Рэмбо первая кровь",
    "Эм, ну не знаю", "Привет, как дела",
    "И тут пришёл Рэм",                       # о нём, а не к нему: без запятой
    "Вчера смотрел аниме про Рэм и Рам.",
])
def test_wake_rejected(text):
    assert find_wake(text, "рэм")[0] is False


def test_near_spelling_rejected_if_real_word():
    real = {"крем", "рам"}
    assert find_wake("Крем для рук", "рэм", lambda w: w in real)[0] is False
    assert find_wake("Рам на сервере", "рэм", lambda w: w in real)[0] is False
    assert find_wake("Рям, открой браузер", "рэм", lambda w: w in real) == (True, "открой браузер")


def test_custom_wake_word():
    assert find_wake("Пятница, который час?", "пятница") == (True, "который час")


# ——— ответ модели ———

def test_validate_clamps_and_filters():
    raw = {"actions": [
        {"skill": "volume_set", "args": {"level": 150}},
        {"skill": "format_disk", "args": {}},                       # такого умения нет
        {"skill": "open_folder", "args": {"folder": "system32"}},   # не из списка
        {"skill": "open_app", "args": {}},                           # нет обязательного
    ], "reply": " "}
    plan = validate(raw, SKILLS)
    assert plan.actions == [("volume_set", {"level": 100})]
    assert plan.reply == ""


def test_validate_optional_params():
    plan = validate({"actions": [{"skill": "set_timer", "args": {"seconds": "90"}}], "reply": ""}, SKILLS)
    assert plan.actions == [("set_timer", {"seconds": 90})]


def test_no_fake_success_without_action():
    plan = validate({"actions": [], "reply": "Компьютер выключен."}, SKILLS)
    assert plan.actions == [] and plan.reply == ""
    plan = validate({"actions": [], "reply": "Этого я пока не умею."}, SKILLS)
    assert plan.reply == "Этого я пока не умею."


def test_schema_lists_every_skill():
    schema = build_schema(SKILLS)
    consts = {v["properties"]["skill"]["const"] for v in schema["properties"]["actions"]["items"]["anyOf"]}
    assert consts == {s.name for s in SKILLS}


def test_disabled_and_confirm_overrides():
    cfg = {**cfgmod.DEFAULTS, "disabled_skills": ["screenshot"], "confirm_overrides": {"lock_pc": True}}
    skills = {s.name: s for s in active_skills(cfg)}
    assert "screenshot" not in skills
    assert skills["lock_pc"].confirm is True
    assert {s.name: s for s in SKILLS}["lock_pc"].confirm is False     # оригинал не тронут


# ——— пользовательские умения ———

def test_parse_keys():
    assert parse_keys("win+shift+s") == [0x5B, 0x10, ord("S")]
    assert parse_keys("Ctrl + F5") == [0x11, 0x74]
    with pytest.raises(ValueError):
        parse_keys("win+суперкнопка")


def test_build_custom():
    skills = build_custom([
        {"title": "Открыть Discord", "type": "open", "target": "C:/d.lnk", "phrases": "дискорд, дис"},
        {"title": "", "type": "open", "target": "x"},                 # пустое — пропускаем
        {"title": "Скриншот области", "type": "keys", "target": "win+shift+s", "confirm": True},
    ])
    assert [s.title for s in skills] == ["Открыть Discord", "Скриншот области"]
    assert "дискорд" in skills[0].examples and skills[1].confirm


# ——— программы ———

def test_app_index(tmp_path: Path):
    for name in ["Discord", "Telegram Desktop", "Steam", "Uninstall Steam", "Google Chrome"]:
        (tmp_path / f"{name}.lnk").write_text("")
    idx = AppIndex([tmp_path])
    assert "uninstall steam" not in idx.apps
    assert idx.resolve("Discord")[0] == "discord"
    assert idx.resolve("дискорд")[0] == "discord"          # транслитерация
    assert idx.resolve("телега")[0] == "telegram desktop"  # синоним
    assert idx.resolve("хром")[0] == "google chrome"
    assert idx.resolve("калькулятор") == ("калькулятор", "calc.exe")
    assert idx.resolve("фотошоп") is None


# ——— мелочи ———

def test_site_url():
    assert site_url("youtube.com") == "https://youtube.com"
    assert site_url("ютуб") == "https://youtube.com"
    assert site_url("https://ya.ru/x") == "https://ya.ru/x"
    assert site_url("кулинарный блог").startswith("https://www.google.com/search?q=")


def test_russian_text():
    assert plural(1, "минута", "минуты", "минут") == "минута"
    assert plural(3, "минута", "минуты", "минут") == "минуты"
    assert plural(11, "минута", "минуты", "минут") == "минут"
    assert duration_ru(90) == "1 минуту 30 секунд"
    assert elapsed_ru(300) == "5 минут прошли"
    assert elapsed_ru(60) == "1 минута прошла" and elapsed_ru(21 * 60) == "21 минута прошла"
    assert elapsed_ru(3600) == "1 час прошёл" and elapsed_ru(5400) == "1 час 30 минут прошли"
    assert elapsed_ru(45) == "45 секунд прошли" and elapsed_ru(11 * 60) == "11 минут прошли"
    assert translit("дискорд") == "diskord"
    assert tell_time(None, dt.datetime(2026, 1, 1, 14, 5)) == "14 часов 5 минут."
    assert tell_time(None, dt.datetime(2026, 1, 1, 21, 0)) == "Ровно 21 час."
    assert tell_date(None, dt.datetime(2026, 10, 4)) == "Сегодня воскресенье, 4 октября."


def test_timers():
    fired = []
    t = Timers(fired.append)
    t.start(0.1, "готово")
    t.start(5, "не успеет")
    time.sleep(0.3)
    assert fired == ["готово"]
    assert t.cancel_all() == 1


def test_config_roundtrip_and_broken_file(tmp_path: Path):
    p = tmp_path / "config.json"
    cfgmod.save({**cfgmod.DEFAULTS, "wake_word": "пятница"}, p)
    assert cfgmod.load(p)["wake_word"] == "пятница"
    p.write_text("{битый json", encoding="utf-8")
    assert cfgmod.load(p)["wake_word"] == "рэм"                   # не падаем
    assert (tmp_path / "config.broken.json").exists()


# ——— ядро без микрофона и модели ———

class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def chat(self, *a, **k):
        self.calls += 1
        return self.answer

    chat_tools = chat


def test_assistant_fast_path_skips_model(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "app_dir", lambda: tmp_path)
    client = FakeClient({"actions": [], "reply": ""})
    a = Assistant(dict(cfgmod.DEFAULTS), client=client, apps=AppIndex([]))
    a.journal = tmp_path / "actions.log"
    plan = a.handle("громкость 30", dry_run=True)
    assert plan.source == "fast" and client.calls == 0
    rec = json.loads(a.journal.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["text"] == "громкость 30"


def test_assistant_uses_model_and_confirm_blocks_without_mic(tmp_path):
    client = FakeClient({"actions": [{"skill": "shutdown_pc", "args": {}}], "reply": ""})
    a = Assistant(dict(cfgmod.DEFAULTS), client=client, apps=AppIndex([]))
    a.journal = tmp_path / "actions.log"
    plan = a.handle("вырубай комп нафиг")
    assert client.calls == 1 and plan.actions == [("shutdown_pc", {})]
    rec = json.loads(a.journal.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["actions"][0][2] == "отменено"        # без микрофона подтвердить нельзя → не выключаем


def test_game_mode_fast_only(tmp_path):
    client = FakeClient({"actions": [{"skill": "open_app", "args": {"name": "Steam"}}], "reply": ""})
    a = Assistant({**cfgmod.DEFAULTS, "game_mode": "fast_only"}, client=client, apps=AppIndex([]))
    a.game_mode = True
    assert a.plan("громче").source == "fast"
    assert a.plan("открой мне стим").source == "fast"            # известная программа — без модели
    assert a.plan("найди рецепт борща").source == "game" and client.calls == 0


def test_say_numbers():
    from rem.text import say_numbers
    assert say_numbers("8 часов 15 минут.") == "восемь часов пятнадцать минут."
    assert say_numbers("21 минута прошла") == "двадцать одна минута прошла"
    assert say_numbers("Таймер на 1 минуту.") == "Таймер на одну минуту."
    assert say_numbers("Сегодня понедельник, 5 октября.") == "Сегодня понедельник, пятое октября."
    assert say_numbers("Громкость 35%.") == "Громкость тридцать пять процентов."
    assert say_numbers("2026") == "две тысячи двадцать шесть"


class CloudDown(FakeClient):
    """Облако не отвечает, модель на компьютере — отвечает."""
    def __init__(self, answer):
        super().__init__(answer)
        self.models_used = []

    def chat_tools(self, model, *a, **k):
        from rem.brain import OllamaError
        self.models_used.append(model)
        if model.endswith("cloud"):
            raise OllamaError('Ollama ответила 401: {"error":"unauthorized"}')
        return self.chat()


def test_cloud_falls_back_to_local_and_warns_once(tmp_path):
    client = CloudDown({"actions": [{"skill": "web_search", "args": {"query": "борщ"}}], "reply": ""})
    a = Assistant({**cfgmod.DEFAULTS, "cloud_model": "gpt-oss:120b-cloud"}, client=client, apps=AppIndex([]))
    notes = []
    a.notify_cb = notes.append
    a.journal = tmp_path / "actions.log"
    plan = a.plan("найди рецепт борща")
    assert plan.source == "llm" and plan.actions == [("web_search", {"query": "борщ"})]
    assert client.models_used == ["gpt-oss:120b-cloud", cfgmod.DEFAULTS["model"]]
    a.plan("найди рецепт щей")
    assert len(notes) == 1 and "аккаунт" in notes[0]


def test_game_mode_uses_cloud(tmp_path):
    client = FakeClient({"actions": [{"skill": "web_search", "args": {"query": "борщ"}}], "reply": ""})
    a = Assistant({**cfgmod.DEFAULTS, "cloud_model": "x:cloud"}, client=client, apps=AppIndex([]))
    a.game_mode = True
    plan = a.plan("найди рецепт борща")
    assert plan.source == "cloud" and client.calls == 1


def test_windows_context_goes_to_model():
    from rem.brain import Brain, windows_context
    seen = []

    class C(FakeClient):
        def chat_tools(self, model, system, user, *a, **k):
            seen.append(user)
            return self.answer
    ctx = windows_context([{"title": "Отчёт.docx — Word", "exe": "WINWORD.EXE", "active": True}])
    Brain(dict(cfgmod.DEFAULTS), active_skills(cfgmod.DEFAULTS), C({"actions": [], "reply": "Word"})).plan(
        "что у меня открыто", context=ctx)
    assert "WINWORD: «Отчёт.docx — Word» (активное)" in seen[0] and seen[0].endswith("Команда: что у меня открыто")


class Steps(FakeClient):
    """Первый ответ — вызов; на продолжении (видит результат) — следующий ответ из списка."""
    def __init__(self, *answers):
        super().__init__(answers[0])
        self.answers, self.seen = list(answers), []

    def chat_tools(self, model, system, user, tools, extra=None, **k):
        self.calls += 1
        self.seen.append((user, extra or []))
        return self.answers[min(len(self.seen) - 1, len(self.answers) - 1)]


def test_run_command_asks_with_description_and_model_retells(tmp_path, monkeypatch):
    from rem.skills import REGISTRY
    client = Steps({"actions": [{"skill": "run_command", "args": {
        "command": "Get-PSDrive C", "description": "узнать место на диске C"}}], "reply": ""},
        {"actions": [], "reply": "На диске C свободно 120 гигабайт."})
    a = Assistant(dict(cfgmod.DEFAULTS), client=client, apps=AppIndex([]))
    a.journal = tmp_path / "actions.log"
    asked, notes, said = [], [], []
    monkeypatch.setattr(a, "_confirm", lambda q: asked.append(q) or True)
    monkeypatch.setattr(REGISTRY["run_command"], "handler", lambda ctx, **k: "Free 120 GB")
    a.reload()
    a.notify_cb = notes.append
    a.say = said.append
    a.handle("сколько места на диске це")
    assert asked == ["Узнать место на диске C"] and notes == ["Команда: Get-PSDrive C"]
    assert said == ["На диске C свободно 120 гигабайт."]
    tool_msg = client.seen[1][1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"] == "Free 120 GB"


def test_multi_step_find_then_open(tmp_path, monkeypatch):
    from rem.skills import REGISTRY
    opened = []
    monkeypatch.setattr(REGISTRY["find_files"], "handler", lambda ctx, **k: r"C:\Docs\Отчёт.docx (изменён 04.10.2026 18:20)")
    monkeypatch.setattr(REGISTRY["open_file"], "handler", lambda ctx, path: opened.append(path))
    client = Steps({"actions": [{"skill": "find_files", "args": {"query": "отчёт"}}], "reply": ""},
                   {"actions": [{"skill": "open_file", "args": {"path": r"C:\Docs\Отчёт.docx"}}], "reply": ""})
    a = Assistant(dict(cfgmod.DEFAULTS), client=client, apps=AppIndex([]))
    a.journal = tmp_path / "actions.log"
    a.say = lambda t: None
    a.handle("открой вчерашний отчёт")
    assert opened == [r"C:\Docs\Отчёт.docx"] and client.calls == 2     # open_file данных не возвращает — конец


def test_no_answer_after_no(tmp_path, monkeypatch):
    client = Steps({"actions": [{"skill": "run_command", "args": {"command": "x", "description": "y"}}], "reply": ""})
    a = Assistant(dict(cfgmod.DEFAULTS), client=client, apps=AppIndex([]))
    a.journal = tmp_path / "actions.log"
    monkeypatch.setattr(a, "_confirm", lambda q: False)
    a.say = lambda t: None
    a.handle("сделай что-нибудь")
    assert client.calls == 1


def test_recent_commands_in_context(tmp_path):
    client = Steps({"actions": [{"skill": "close_app", "args": {"name": "Chrome"}}], "reply": ""})
    a = Assistant(dict(cfgmod.DEFAULTS), client=client, apps=AppIndex([]))
    a.journal = tmp_path / "actions.log"
    a.say = lambda t: None
    a.handle("переключись на хром")                   # быстрый путь — модель не спрашиваем
    a.handle("закрой его")
    user = client.seen[0][0]
    assert "Недавно" in user and "«переключись на хром» → switch_window" in user and "Сейчас:" in user


def test_call_written_as_text():
    from rem.brain import text_calls
    assert text_calls("volume_mute()") == [{"skill": "volume_mute", "args": {}}]
    assert text_calls('volume_set(level=30), web_search(query="блины")')[1]["args"] == {"query": "блины"}
    assert text_calls("Здравствуйте! Рэм слушает (внимательно).") == []
