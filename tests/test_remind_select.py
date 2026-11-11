"""Напоминания по времени суток и выделенный текст — без микрофона, модели и Windows."""
import datetime as dt
import json

import pytest

from rem import config as cfgmod
from rem.assistant import Assistant, Context
from rem.fastpath import FastPath
from rem.reminders import Reminders, due_phrase, parse, say_when
from rem.skills import REGISTRY, active_skills
from rem.skills.apps import AppIndex
from rem.skills.selected import cut, target_lang
from rem.timers import Timers

NOW = dt.datetime(2026, 11, 11, 14, 20)          # среда


def at(text, now=NOW):
    w = parse(text, now)
    return None if w is None else (w.at.strftime("%d %H:%M"), w.text, w.alarm)


@pytest.mark.parametrize("text,want", [
    ("Напомни в семь вечера купить хлеба", ("11 19:00", "купить хлеба", False)),
    ("Рэм, напомни мне завтра в 8:30 позвонить маме.", ("12 08:30", "позвонить маме", False)),
    ("Разбуди меня в 7", ("12 07:00", "", True)),
    ("Поставь будильник на 6:45", ("12 06:45", "", True)),
    ("будильник на завтра на 7 утра", ("12 07:00", "", True)),
    ("напомни через 20 минут выключить плиту", ("11 14:40", "выключить плиту", False)),
    ("Напомни через полчаса проверить духовку", ("11 14:50", "проверить духовку", False)),
    ("Напомни через 2 часа 15 минут", ("11 16:35", "", False)),
    ("напомни в половине восьмого о встрече", ("11 19:30", "о встрече", False)),
    ("напомни полвосьмого", ("11 19:30", "", False)),
    ("напомни без четверти 8 выйти из дома", ("11 19:45", "выйти из дома", False)),
    ("Напомни в 3 забрать посылку", ("11 15:00", "забрать посылку", False)),     # днём «в 3» — 15:00
    ("напомни завтра в 3 забрать посылку", ("12 15:00", "забрать посылку", False)),
    ("напомни завтра в 9 записаться к врачу", ("12 09:00", "записаться к врачу", False)),
    ("напомни в 11 ночи выключить компьютер", ("11 23:00", "выключить компьютер", False)),
    ("напомни в полночь выпить таблетку", ("12 00:00", "выпить таблетку", False)),
    ("напомни в пятницу в 10 утра про отчёт", ("13 10:00", "про отчет", False)),
    ("напомни 12 ноября в 10:00 оплатить интернет", ("12 10:00", "оплатить интернет", False)),
    ("напомни завтра утром полить цветы", ("12 09:00", "полить цветы", False)),
    ("Напомни в 19 30 о том, что нужно позвонить", ("11 19:30", "нужно позвонить", False)),
    ("напомни к семи забрать ребёнка", ("11 19:00", "забрать ребенка", False)),
    ("напомни в 10 часов 15 минут поесть", ("11 22:15", "поесть", False)),
    ("Напомни во вторник в 18:00 тренировка", ("17 18:00", "тренировка", False)),
    ("напомни в 7 утра", ("12 07:00", "", False)),                # утро уже прошло — завтра
    ("Напомни купить хлеб", None),                                # когда? — переспросит умение
    ("Таймер на 5 минут", None),
    ("открой браузер", None),
])
def test_parse(text, want):
    assert at(text) == want


def test_past_time_today_is_kept_for_answer():
    w = parse("напомни сегодня в 9 утра", NOW)
    assert w.at < NOW


def test_say_when_and_due_phrase():
    assert say_when(dt.datetime(2026, 11, 11, 19, 0), NOW) == "сегодня в 19:00"
    assert say_when(dt.datetime(2026, 11, 12, 7, 5), NOW) == "завтра в 7:05"
    assert say_when(dt.datetime(2026, 11, 17, 9, 0), NOW) == "во вторник в 9:00"
    assert say_when(dt.datetime(2026, 12, 1, 9, 0), NOW) == "1 декабря в 9:00"
    assert say_when(NOW + dt.timedelta(minutes=20), NOW, relative=True) == "через 20 минут"
    assert due_phrase({"text": "купить хлеб", "at": "2026-11-11T19:00"}) == "Напоминаю: купить хлеб."
    assert due_phrase({"text": "про отчет", "at": "2026-11-11T19:00"}) == "Напоминаю про отчет."
    assert due_phrase({"kind": "alarm", "text": "", "at": "2026-11-12T07:00"}) == "Доброе утро! Уже 7:00."


def test_fast_path_routes_reminder_phrases():
    fp = FastPath(active_skills(dict(cfgmod.DEFAULTS)))
    assert fp.match("Напомни в семь вечера купить хлеб").actions[0][0] == "set_reminder"
    assert fp.match("отмени будильник на завтра").actions == [("cancel_reminders", {"kind": "alarm"})]
    assert fp.match("Отмени все напоминания.").actions == [("cancel_reminders", {})]
    assert fp.match("какие у меня сейчас напоминания").actions == [("list_reminders", {})]
    assert fp.match("Напомни купить хлеб").actions == [("set_reminder", {
        "at": "", "text": "", "kind": "remind", "relative": "нет"})]
    assert fp.match("таймер на 5 минут").actions == [("set_timer", {"minutes": 5})]
    assert fp.match("Переведи это на английский.").actions == [
        ("selected_text", {"action": "translate", "lang": "английский"})]
    assert fp.match("что тут написано").actions == [("selected_text", {"action": "read"})]


def test_model_does_not_see_new_skills():
    from rem.brain import build_system_prompt
    a = Assistant(dict(cfgmod.DEFAULTS), client=None, apps=AppIndex([]))
    prompt = build_system_prompt(a.brain.skills)
    for name in ("set_reminder", "list_reminders", "cancel_reminders", "selected_text", "dictation"):
        assert name not in prompt


def test_store_survives_restart_fires_and_reports_missed(tmp_path):
    fired = []
    r = Reminders(tmp_path / "r.json", lambda item, missed: fired.append((item["text"], missed)))
    r.add(NOW + dt.timedelta(hours=1), "купить хлеб")
    r.add(NOW + dt.timedelta(days=1), "будильник", "alarm")
    r.add(NOW - dt.timedelta(days=2), "давнее")
    r2 = Reminders(tmp_path / "r.json", lambda item, missed: fired.append((item["text"], missed)))
    assert [x["text"] for x in r2.upcoming()] == ["давнее", "купить хлеб", "будильник"]
    r2.check(NOW)                                  # «давнее» — больше суток назад: молча убрать
    assert fired == [] and len(r2.upcoming()) == 2
    r2.check(NOW + dt.timedelta(hours=1, minutes=2))
    assert fired == [("купить хлеб", False)]
    r2.check(NOW + dt.timedelta(days=1, hours=3))     # компьютер был выключен — скажет, что пропущено
    assert fired[-1] == ("будильник", True) and r2.upcoming() == []
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8")) == []
    r2.add(NOW + dt.timedelta(hours=1), "a")
    r2.add(NOW + dt.timedelta(hours=2), "b", "alarm")
    assert r2.cancel_all("alarm") == 1 and r2.cancel_all() == 1


def test_skills_answers(tmp_path):
    r = Reminders(tmp_path / "r.json", lambda *a: None)
    ctx = Context({}, AppIndex([]), Timers(lambda m: None), lambda t: None, reminders=r)
    soon = (dt.datetime.now() + dt.timedelta(minutes=20)).isoformat(timespec="seconds")
    say = REGISTRY["set_reminder"].handler(ctx, at=soon, text="выключить плиту", relative="да")
    assert say == "Напомню через 20 минут: выключить плиту."
    assert REGISTRY["set_reminder"].handler(ctx, at="").startswith("Когда напомнить?")
    assert "уже прошло" in REGISTRY["set_reminder"].handler(ctx, at="2020-01-01T09:00")
    assert REGISTRY["list_reminders"].handler(ctx).startswith("1 запись: сегодня в") or \
        REGISTRY["list_reminders"].handler(ctx).startswith("1 запись: завтра в")
    assert REGISTRY["cancel_reminders"].handler(ctx, kind="alarm") == "Будильник не стоит."
    assert REGISTRY["cancel_reminders"].handler(ctx) == "Напоминания отменены."


def test_alarm_rings_until_spoken_to(monkeypatch):
    a = Assistant(dict(cfgmod.DEFAULTS), client=None, apps=AppIndex([]))
    said, sounds = [], []
    a.say = said.append
    a._sound = sounds.append
    monkeypatch.setattr("rem.assistant.time.sleep", lambda s: None)
    a._ring({"kind": "alarm", "text": "", "at": "2026-11-12T07:00"}, times=3, gap=0)
    assert said == ["Доброе утро! Уже 7:00."] * 3 and len(sounds) == 9
    said.clear()
    real = a.after_barge
    a._sound = lambda n: real("хватит")              # «Рэм, хватит» во время звонка
    a._ring({"kind": "alarm", "text": "", "at": "2026-11-12T07:00"}, times=3, gap=0)
    assert said == []


# ——— выделенный текст ———

class FakeBrain:
    def __init__(self):
        self.seen = []

    def __call__(self, system, text, tokens=200):
        self.seen.append((system, text, tokens))
        return "Hello, world."


def _ctx(text, brain=None):
    copied, shown = [], []
    ctx = Context({}, AppIndex([]), Timers(lambda m: None), lambda t: None,
                  selection=lambda: (text, "selection"), ask=brain or FakeBrain(),
                  copy=copied.append, show=shown.append)
    return ctx, copied, shown


def test_translate_to_english_shows_and_copies_instead_of_reading():
    brain = FakeBrain()
    ctx, copied, shown = _ctx("Привет, мир.", brain)
    say = REGISTRY["selected_text"].handler(ctx, action="translate")
    assert "английский" in brain.seen[0][0] and brain.seen[0][2] == 700
    assert copied == shown == ["Hello, world."] and "скопировала" in say


def test_translate_to_russian_is_read():
    ctx, copied, _ = _ctx("Hello", lambda s, t, n=200: "Привет.")
    assert REGISTRY["selected_text"].handler(ctx, action="translate") == "Привет." and copied == ["Привет."]


def test_read_long_text_is_cut_by_sentence_and_empty_selection():
    long = "Первое предложение. " * 60
    ctx, _, _ = _ctx(long)
    say = REGISTRY["selected_text"].handler(ctx, action="read")
    assert say.endswith("предложение. И так далее.") and len(say) < 760
    ctx, _, _ = _ctx("   ")
    assert REGISTRY["selected_text"].handler(ctx, action="short").startswith("Ничего не выделено")
    ctx, _, _ = _ctx("текст", lambda s, t, n=200: None)
    assert "игры" in REGISTRY["selected_text"].handler(ctx, action="explain")


def test_helpers():
    assert target_lang("Привет, как дела?") == "английский"
    assert target_lang("Hello there") == "русский"
    assert cut("Раз. Два. Три.", 9) == "Раз. Два."


def test_game_mode_fast_only_does_not_ask_model():
    a = Assistant({**cfgmod.DEFAULTS, "game_mode": "fast_only"}, client=None, apps=AppIndex([]))
    a.game_mode = True
    assert a._ask_text("x", "y") is None


def test_rem_style_phrases(tmp_path):
    """«В стиле Рэм» ответы умений — о себе в третьем лице, без «я»; обычный стиль не меняется."""
    assert due_phrase({"text": "купить хлеб", "at": "2026-11-11T19:00"}, rem=True) == "Вы просили напомнить: купить хлеб."
    assert due_phrase({"text": "", "at": "2026-11-11T19:00"}, rem=True) == "Вы просили напомнить."
    assert due_phrase({"kind": "alarm", "text": "", "at": "2026-11-12T07:00"}, rem=True) == \
        "Доброе утро! Уже 7:00, пора вставать."
    r = Reminders(tmp_path / "r.json", lambda *a: None)
    a = Assistant({**cfgmod.DEFAULTS, "rem_style": True})
    a.ctx.reminders = r
    soon = (dt.datetime.now() + dt.timedelta(minutes=20)).isoformat(timespec="seconds")
    say = REGISTRY["set_reminder"].handler(a.ctx, at=soon, text="выключить плиту", relative="да")
    assert say == "Рэм напомнит через 20 минут: выключить плиту."
    morning = (dt.datetime.now() + dt.timedelta(days=1)).replace(hour=7, minute=0, second=0)
    assert REGISTRY["set_reminder"].handler(a.ctx, at=morning.isoformat(), kind="alarm") == \
        "Рэм разбудит вас завтра в 7:00."
    assert a._not_understood() == "Простите, Рэм не поняла команду."
    a.config["rem_style"] = False
    assert REGISTRY["set_reminder"].handler(a.ctx, at=soon, text="x", relative="да").startswith("Напомню")
