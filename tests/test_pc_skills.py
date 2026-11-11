"""Клавиши, звук, состояние компьютера, погода, калькулятор, курс — без Windows и сети."""
import pytest

from rem import config as cfgmod
from rem.assistant import Context
from rem.fastpath import FastPath
from rem.skills import REGISTRY, active_skills
from rem.skills.apps import AppIndex
from rem.skills.audio import pick_output
from rem.skills.info import city_candidates, to_expr, weather_text

NEW = {"press_keys", "audio_output", "app_volume", "weather", "calculate", "currency", "pc_status",
       "window_control", "youtube"}


@pytest.fixture(scope="module")
def fast():
    return FastPath(active_skills(dict(cfgmod.DEFAULTS)))


def ctx(**cfg):
    return Context(dict(cfg), AppIndex([]), None, lambda t: None)


@pytest.mark.parametrize("said, skill, args", [
    ("скопируй", "press_keys", {"action": "copy"}),
    ("выдели всё", "press_keys", {"action": "select_all"}),
    ("закрой вкладку", "press_keys", {"action": "close_tab"}),
    ("обнови страницу", "press_keys", {"action": "reload"}),
    ("листай вниз", "press_keys", {"action": "scroll_down"}),
    ("нажми энтер", "press_keys", {"action": "enter"}),
    ("переключи звук на наушники", "audio_output", {"target": "наушники"}),
    ("переключи звук", "audio_output", {"target": ""}),
    ("сделай дискорд тише", "app_volume", {"app": "дискорд", "level": -1}),
    ("громкость дискорда 30", "app_volume", {"app": "дискорда", "level": 30}),
    ("выключи звук в браузере", "app_volume", {"app": "браузере", "level": 0}),
    ("сколько заряда", "pc_status", {"what": "battery"}),
    ("сколько места на диске", "pc_status", {"what": "disk"}),
    ("что грузит компьютер", "pc_status", {"what": "load"}),
    ("закрой то что тормозит", "pc_status", {"what": "close_heavy"}),
    ("какая погода", "weather", {"city": "", "day": "today"}),
    ("погода завтра в Казани", "weather", {"city": "казани", "day": "tomorrow"}),
    ("нужен ли зонт", "weather", {"city": "", "day": "umbrella"}),
    ("мой город Казань", "weather", {"city": "казань", "day": "home"}),
    ("сколько будет 15 процентов от 2300", "calculate", {"expression": "15/100*2300"}),
    ("посчитай 340 умножить на 12", "calculate", {"expression": "340*12"}),
    ("сколько будет 2,5 умножить на 4", "calculate", {"expression": "2.5*4"}),
    ("курс доллара", "currency", {"amount": 1.0, "src": "USD", "dst": "RUB"}),
    ("сколько долларов в 5000 рублей", "currency", {"amount": 5000.0, "src": "RUB", "dst": "USD"}),
    ("переведи 50 евро в рубли", "currency", {"amount": 50.0, "src": "EUR", "dst": "RUB"}),
])
def test_routes(fast, said, skill, args):
    plan = fast.match(said)
    assert plan is not None and plan.actions == [(skill, args)]


@pytest.mark.parametrize("said", ["отмени", "сделай музыку тише", "громкость на 30 процентов", "открой погоду",
                                  "сколько времени", "отмени таймер", "включи звук", "сделай тише",
                                  "какой сегодня день", "открой браузер"])
def test_old_commands_not_taken(fast, said):
    plan = fast.match(said)
    assert plan is None or plan.actions[0][0] not in NEW, plan


def test_old_examples_still_route_to_old_skills(fast):
    """Ни один пример старых умений не уходит в новые."""
    fill = {"{app}": "телеграм", "{site}": "ютуб", "{q}": "рецепт борща", "{label}": "сохранить", "{n}": "30"}
    for s in active_skills(dict(cfgmod.DEFAULTS)):
        if s.name in NEW:
            continue
        for ex in s.examples:
            ex = ex[0] if isinstance(ex, tuple) else ex
            for k, v in fill.items():
                ex = ex.replace(k, v)
            plan = fast.match(ex)
            assert plan is None or plan.actions[0][0] not in NEW, (ex, plan)


def test_calculator():
    calc = REGISTRY["calculate"].handler
    assert calc(ctx(), expression="15/100*2300") == "Будет 345."
    assert calc(ctx(), expression="10/3") == "Будет 3,33."
    assert calc(ctx(), expression="2-5") == "Будет минус 3."
    assert calc(ctx(), expression="100/0") == "На ноль делить нельзя."
    assert calc(ctx(), expression="__import__('os')").startswith("Не получилось")
    assert to_expr("сколько будет корень из 144") == "sqrt(144)"
    assert to_expr("сколько будет 12 в квадрате") == "12**2"
    assert to_expr("сколько будет два плюс два") == "2+2"
    assert to_expr("сколько у меня окон") is None


def test_currency(monkeypatch):
    from rem.skills import info
    monkeypatch.setattr(info, "rates", lambda: {"RUB": 1.0, "USD": 82.66, "CNY": 12.39})
    cur = REGISTRY["currency"].handler
    assert cur(ctx(), amount=1.0, src="USD", dst="RUB") == "Курс ЦБ: доллар — 82,66 рубля."
    assert cur(ctx(), amount=5000.0, src="RUB", dst="USD") == "5000 рублей — это 60,49 доллара по курсу ЦБ."
    assert cur(ctx(), amount=1.0, src="XYZ", dst="RUB") == "Такой валюты нет в курсах ЦБ."


DATA = {"current": {"temperature_2m": -2.4, "apparent_temperature": -6.8, "weather_code": 3, "wind_speed_10m": 4.2},
        "daily": {"temperature_2m_min": [-5.0, 0.2], "temperature_2m_max": [1.0, 1.4],
                  "precipitation_probability_max": [13, 98], "weather_code": [3, 61]}}


def test_weather_text():
    assert weather_text(DATA, "today") == ("Сейчас минус 2 градуса, ощущается как минус 7, пасмурно, ветер 4 метра "
                                          "в секунду. Сегодня от минус 5 до 1 градуса, вероятность осадков 13 процентов.")
    assert weather_text(DATA, "tomorrow", "Казань") == \
        "Казань, завтра от 0 до 1 градуса, небольшой дождь, вероятность осадков 98 процентов."
    assert weather_text(DATA, "umbrella").startswith("Зонт, скорее всего, не нужен")
    assert "казань" in city_candidates("казани") and "москва" in city_candidates("москве")
    assert city_candidates("питере") == ["Санкт-Петербург"]


def test_weather_asks_city_and_remembers(monkeypatch):
    from rem.skills import info
    monkeypatch.setattr(info, "geocode", lambda c: {"name": "Казань", "latitude": 55.8, "longitude": 49.1})
    monkeypatch.setattr(info, "_get", lambda url, timeout=8.0: DATA)
    monkeypatch.setattr(info, "_save", lambda ctx: None)
    c = ctx()
    w = REGISTRY["weather"].handler
    assert w(c).startswith("В каком городе?")
    assert w(c, city="казани").startswith("Казань, сейчас минус 2")
    assert c.config["weather_city"] == "Казань"
    assert w(c).startswith("Сейчас минус 2")                 # свой город — без названия


OUTS = [{"id": "1", "name": "Динамики (Realtek High Definition Audio)", "default": True},
        {"id": "2", "name": "Наушники (WH-1000XM4 Stereo)", "default": False},
        {"id": "3", "name": "LG ULTRAGEAR (NVIDIA High Definition Audio)", "default": False}]


def test_pick_output():
    assert pick_output("наушники", OUTS)["id"] == "2"
    assert pick_output("колонки", OUTS)["id"] == "1"
    assert pick_output("монитор", OUTS)["id"] == "3"
    assert pick_output("", OUTS)["id"] == "2"                 # следующее по кругу
    assert pick_output("телевизор", OUTS[:2]) is None


def test_audio_output_switches(monkeypatch):
    from rem.skills import audio
    switched = []
    monkeypatch.setattr(audio.winapi, "audio_outputs", lambda: OUTS)
    monkeypatch.setattr(audio.winapi, "set_audio_output", switched.append)
    h = REGISTRY["audio_output"].handler
    assert h(ctx(), target="наушники") == "Звук теперь в «Наушники (WH-1000XM4 Stereo)»." and switched == ["2"]
    assert h(ctx(), target="колонки").startswith("Звук и так идёт")
    monkeypatch.setattr(audio.winapi, "audio_outputs", lambda: [OUTS[0], OUTS[2]])
    assert h(ctx(), target="наушники").startswith("Такого устройства нет. Включены: Динамики")


def test_app_volume(monkeypatch):
    from rem.skills import audio

    class Vol:
        level, muted = 0.5, 0

        def GetMasterVolume(self):
            return self.level

        def SetMasterVolume(self, v, ctx):
            self.level = v

        def SetMute(self, m, ctx):
            self.muted = m
    v = Vol()
    monkeypatch.setattr(audio, "_app_pids", lambda ctx, name: {1})
    monkeypatch.setattr(audio.winapi, "app_audio", lambda pids: [v] if pids else [])
    h = REGISTRY["app_volume"].handler
    assert h(ctx(), app="дискорд", level=-1) is None and abs(v.level - 0.3) < 1e-9
    assert h(ctx(), app="дискорд", level=80) is None and v.level == 0.8
    assert h(ctx(), app="дискорд", level=0) is None and v.muted == 1
    monkeypatch.setattr(audio, "_app_pids", lambda ctx, name: set())
    assert "не играет звук" in h(ctx(), app="скайп", level=-1)


def test_press_keys(monkeypatch):
    from rem.skills import keys
    pressed, scrolled = [], []
    monkeypatch.setattr(keys.winapi, "foreground_window", lambda: {"hwnd": 5})
    monkeypatch.setattr(keys.winapi, "is_foreground", lambda h: True)
    monkeypatch.setattr(keys.winapi, "press", lambda *k: pressed.append(k))
    monkeypatch.setattr(keys.winapi, "scroll", scrolled.append)
    h = REGISTRY["press_keys"].handler
    h(ctx(), action="copy")
    h(ctx(), action="reopen_tab")
    h(ctx(), action="scroll_down")
    vk = keys.winapi.VK
    assert pressed == [(vk["ctrl"], vk["c"]), (vk["ctrl"], vk["shift"], vk["t"])] and scrolled == [-5]


def test_pc_status_battery(monkeypatch):
    import psutil

    from rem.skills import pcstatus
    Bat = type("B", (), {})
    b = Bat()
    b.percent, b.power_plugged, b.secsleft = 76, False, 3 * 3600 + 600
    monkeypatch.setattr(psutil, "sensors_battery", lambda: b)
    assert pcstatus.battery_text() == "Заряд 76 процентов, хватит примерно на 3 часа 10 минут."
    monkeypatch.setattr(psutil, "sensors_battery", lambda: None)
    assert "нет батареи" in pcstatus.battery_text()


def test_close_heavy_asks_first(monkeypatch):
    from rem.skills import pcstatus
    closed, asked = [], []
    monkeypatch.setattr(pcstatus, "heavy", lambda sample_s=1.0: [
        {"name": "chrome", "cpu": 41.0, "mem": 3 * 1024 ** 3, "pids": {10, 11}}])
    monkeypatch.setattr(pcstatus.winapi, "open_windows", lambda: [{"hwnd": 7, "pid": 10}])
    monkeypatch.setattr(pcstatus.winapi, "close_windows", closed.append)
    c = Context({}, AppIndex([]), None, lambda t: None, lambda q: asked.append(q) or False)
    assert REGISTRY["pc_status"].handler(c, what="close_heavy") == "Хорошо, не закрываю."
    assert asked == ["Сильнее всего тормозит chrome: грузит процессор на 41 процент. Закрыть"] and not closed
    c.confirm = lambda q: True
    assert REGISTRY["pc_status"].handler(c, what="close_heavy") is None and closed == [[7]]
