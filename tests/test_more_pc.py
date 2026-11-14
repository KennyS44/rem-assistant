"""Экран, файлы, сеть, выключение по таймеру, раскладка, «не беспокоить», рабочие столы — без Windows."""
import os

import pytest

from rem import config as cfgmod
from rem import winapi
from rem.assistant import Context
from rem.fastpath import FastPath
from rem.skills import REGISTRY, active_skills
from rem.skills import network as netmod
from rem.skills.apps import AppIndex

NEW = {"display", "file_actions", "network", "shutdown_timer", "keyboard_layout", "do_not_disturb"}


@pytest.fixture(scope="module")
def fast():
    return FastPath(active_skills(dict(cfgmod.DEFAULTS)))


def ctx(answer=True):
    asked = []
    c = Context({}, AppIndex([]), None, lambda t: None, lambda q: asked.append(q) or answer)
    return c, asked


@pytest.mark.parametrize("said, skill, args", [
    ("сделай ярче", "display", {"action": "up"}),
    ("Сделай экран темнее.", "display", {"action": "down"}),
    ("прибавь яркость", "display", {"action": "up"}),
    ("убавь яркость", "display", {"action": "down"}),
    ("яркость 50", "display", {"action": "set", "level": 50}),
    ("поставь яркость на тридцать процентов", "display", {"action": "set", "level": 30}),
    ("яркость на максимум", "display", {"action": "set", "level": 100}),
    ("какая яркость", "display", {"action": "ask"}),
    ("включи ночной свет", "display", {"action": "night_on"}),
    ("выключи ночной режим", "display", {"action": "night_off"}),
    ("выключи экран", "display", {"action": "off"}),
    ("погаси монитор", "display", {"action": "off"}),
    ("включи экран", "display", {"action": "on"}),
    ("открой последнюю загрузку", "file_actions", {"action": "open", "target": "download", "name": ""}),
    ("открой последний скачанный файл", "file_actions", {"action": "open", "target": "download", "name": ""}),
    ("покажи последний скриншот", "file_actions", {"action": "show", "target": "screenshot", "name": ""}),
    ("удали последнюю загрузку", "file_actions", {"action": "delete", "target": "download", "name": ""}),
    ("удали выделенные файлы", "file_actions", {"action": "delete", "target": "selected", "name": ""}),
    ("переименуй последний скриншот в Отчёт за май", "file_actions",
     {"action": "rename", "target": "screenshot", "name": "Отчёт за май"}),
    ("перемести этот файл в документы", "file_actions", {"action": "move", "target": "selected", "name": "documents"}),
    ("перенеси последнюю загрузку на рабочий стол", "file_actions",
     {"action": "move", "target": "download", "name": "desktop"}),
    ("очисти корзину", "file_actions", {"action": "empty_bin", "target": "selected", "name": ""}),
    ("включи вай фай", "network", {"action": "wifi_on"}),
    ("выключи wi-fi", "network", {"action": "wifi_off"}),
    ("включи блютуз", "network", {"action": "bt_on"}),
    ("отключи bluetooth", "network", {"action": "bt_off"}),
    ("к какой сети я подключен", "network", {"action": "which"}),
    ("есть ли интернет", "network", {"action": "internet"}),
    ("проверь интернет", "network", {"action": "internet"}),
    ("интернет пропал", "network", {"action": "internet"}),
    ("выключи компьютер через час", "shutdown_timer", {"minutes": 60}),
    ("выключи комп через 30 минут", "shutdown_timer", {"minutes": 30}),
    ("выключи компьютер через полтора часа", "shutdown_timer", {"minutes": 90}),
    ("отмени выключение", "shutdown_timer", {"minutes": 0}),
    ("не выключай компьютер", "shutdown_timer", {"minutes": 0}),
    ("переключи раскладку", "keyboard_layout", {"lang": ""}),
    ("смени язык", "keyboard_layout", {"lang": ""}),
    ("английская раскладка", "keyboard_layout", {"lang": "en"}),
    ("переключи на русский", "keyboard_layout", {"lang": "ru"}),
    ("включи не беспокоить", "do_not_disturb", {"on": "on"}),
    ("не беспокоить", "do_not_disturb", {"on": "on"}),
    ("выключи режим не беспокоить", "do_not_disturb", {"on": "off"}),
    ("включи уведомления", "do_not_disturb", {"on": "off"}),
    ("новый рабочий стол", "press_keys", {"action": "desktop_new"}),
    ("следующий рабочий стол", "press_keys", {"action": "desktop_next"}),
    ("закрой рабочий стол", "press_keys", {"action": "desktop_close"}),
])
def test_routes(fast, said, skill, args):
    plan = fast.match(said)
    assert plan is not None and plan.actions == [(skill, args)], plan


@pytest.mark.parametrize("said", ["выключи компьютер", "покажи рабочий стол", "открой рабочий стол",
                                  "сделай тише", "сделай громче", "открой загрузки", "удали это",
                                  "переведи на английский", "перенеси окно на второй монитор",
                                  "отправь это", "убери это", "напомни через час выключить компьютер",
                                  "выключи компьютер через час и закрой браузер", "включи звук",
                                  "открой документы", "сверни все окна"])
def test_old_commands_not_taken(fast, said):
    plan = fast.match(said)
    assert plan is None or plan.actions[0][0] not in NEW, plan


def test_old_examples_still_route_to_old_skills(fast):
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


def test_new_skills_stay_out_of_model_prompt():
    from rem.brain import build_system_prompt
    skills = active_skills(dict(cfgmod.DEFAULTS))
    prompt = build_system_prompt([s for s in skills if s.model])
    assert not any(n in prompt for n in NEW)


def test_shutdown_at_clock_time():
    from rem.skills.desk import _parse_shutdown
    args = _parse_shutdown("выключи компьютер в 23:30")
    assert args is not None and 1 <= args["minutes"] <= 24 * 60


def test_night_light_blob_round_trip():
    off = bytearray(range(41))
    off[18] = 0x13
    off = bytes(off)
    on = winapi.night_light_blob(off, True)
    assert on[18] == 0x15 and len(on) == len(off) + 2 and on[23:25] == b"\x10\x00"
    assert on[10:15] != off[10:15]                         # метка времени сдвинулась
    back = winapi.night_light_blob(on, False)
    assert back[18] == 0x13 and len(back) == len(off) and back[23:] == off[23:]
    assert winapi.night_light_blob(on, True) == on         # уже включён — без изменений
    assert winapi.night_light_blob(b"\x00" * 41, True) is None   # незнакомый формат не трогаем


def test_brightness_reply(monkeypatch):
    c, _ = ctx()
    monkeypatch.setattr(winapi, "brightness", lambda set_to=None, step=0: [min(100, 60 + step)])
    assert REGISTRY["display"].handler(c, "up") == "Яркость 80 процентов."
    monkeypatch.setattr(winapi, "brightness", lambda set_to=None, step=0: [])
    assert "DDC/CI" in REGISTRY["display"].handler(c, "down")


def test_internet_text(monkeypatch):
    monkeypatch.setattr(netmod, "check_internet", lambda: (True, True, 40))
    assert netmod.internet_text() == "Интернет есть и работает быстро."
    monkeypatch.setattr(netmod, "check_internet", lambda: (True, False, 40))
    assert "DNS" in netmod.internet_text()
    monkeypatch.setattr(netmod, "check_internet", lambda: (False, False, 3000))
    assert netmod.internet_text() == "Интернета нет."


def test_which_network(monkeypatch):
    monkeypatch.setattr(winapi, "connections", lambda: [{"alias": "Беспроводная сеть", "name": "Home", "internet": True}])
    assert netmod.which_text() == "Подключено по Wi‑Fi к сети «Home»."
    monkeypatch.setattr(winapi, "connections", lambda: [{"alias": "Ethernet", "name": "Сеть 2", "internet": False}])
    assert netmod.which_text() == "Подключено по кабелю к сети «Сеть 2» (без интернета)."


def test_radio_reply(monkeypatch):
    c, _ = ctx()
    monkeypatch.setattr(winapi, "radios", lambda kind=None, state=None: {"WiFi": state or "On"})
    assert REGISTRY["network"].handler(c, "wifi_off") == "Wi‑Fi выключен."
    assert REGISTRY["network"].handler(c, "bt_on") == "Bluetooth на этом компьютере нет."


@pytest.fixture
def folders(tmp_path, monkeypatch):
    for n in ("downloads", "documents", "pictures", "videos", "desktop"):
        (tmp_path / n).mkdir()
    monkeypatch.setattr(winapi, "known_folder", lambda n: str(tmp_path / n))
    opened = []
    monkeypatch.setattr(winapi, "open_path", opened.append)
    return tmp_path, opened


def _touch(p, t):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    os.utime(p, (t, t))
    return p


def test_last_download_skips_unfinished_and_programs(folders):
    tmp, opened = folders
    _touch(tmp / "downloads" / "old.pdf", 1000)
    _touch(tmp / "downloads" / "film.mkv.crdownload", 3000)
    c, _ = ctx()
    fa = REGISTRY["file_actions"].handler
    assert fa(c, "open", "download") is None and opened == [str(tmp / "downloads" / "old.pdf")]
    _touch(tmp / "downloads" / "setup.exe", 2000)
    assert "программа" in fa(c, "open", "download") and opened[-1] == str(tmp / "downloads")


def test_last_screenshot(folders):
    tmp, opened = folders
    _touch(tmp / "pictures" / "Screenshots" / "a.png", 1000)
    _touch(tmp / "videos" / "Captures" / "game.png", 2000)
    _touch(tmp / "videos" / "Captures" / "game.mp4", 3000)
    c, _ = ctx()
    REGISTRY["file_actions"].handler(c, "show", "screenshot")
    assert opened == [str(tmp / "videos" / "Captures" / "game.png")]


def test_rename_keeps_extension_and_move(folders):
    tmp, _ = folders
    _touch(tmp / "downloads" / "IMG_123.jpg", 1000)
    c, _ = ctx()
    fa = REGISTRY["file_actions"].handler
    assert fa(c, "rename", "download", "Отпуск") == "Переименовано в «Отпуск.jpg»."
    assert (tmp / "downloads" / "Отпуск.jpg").exists()
    assert fa(c, "move", "download", "pictures") == "Перенесено в папку «изображения»: «Отпуск.jpg»."
    assert (tmp / "pictures" / "Отпуск.jpg").exists()


def test_delete_asks_first(folders, monkeypatch):
    tmp, _ = folders
    _touch(tmp / "downloads" / "a.txt", 1000)
    gone = []
    monkeypatch.setattr(winapi, "recycle", lambda paths: gone.extend(paths) or True)
    c, asked = ctx(answer=False)
    REGISTRY["file_actions"].handler(c, "delete", "download")
    assert asked == ["Удалить «a.txt» в корзину"] and gone == []
    c, asked = ctx(answer=True)
    assert REGISTRY["file_actions"].handler(c, "delete", "download") == "Удалено в корзину."
    assert gone == [str(tmp / "downloads" / "a.txt")]


def test_shutdown_timer_asks_and_cancels(monkeypatch):
    planned = []
    monkeypatch.setattr(winapi, "shutdown_in", lambda s: planned.append(s) or True)
    monkeypatch.setattr(winapi, "shutdown_cancel", lambda: False)
    c, asked = ctx(answer=False)
    REGISTRY["shutdown_timer"].handler(c, 60)
    assert asked == ["Выключить компьютер через 1 час"] and planned == []
    c, asked = ctx(answer=True)
    assert REGISTRY["shutdown_timer"].handler(c, 60).startswith("Компьютер выключится через 1 час.")
    assert planned == [3600]
    assert REGISTRY["shutdown_timer"].handler(c, 0) == "Выключение не было запланировано."
