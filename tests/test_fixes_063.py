"""0.6.3: вкладки любым глаголом, «обнови браузер», «включи музыку», «отправь сообщение» с вопросами,
окна-близнецы, дослушивание оборванной фразы — без Windows."""
import pytest

from rem import config as cfgmod
from rem import uia, winapi
from rem.assistant import Context
from rem.fastpath import FastPath
from rem.listen import unfinished
from rem.skills import REGISTRY, active_skills
from rem.skills import apps as appsmod
from rem.skills import media, screen
from rem.skills.apps import AppIndex
from rem.uia import Control

NEW = {"music", "send_message"}


@pytest.fixture(scope="module")
def fast():
    return FastPath(active_skills(dict(cfgmod.DEFAULTS)))


def ctx(answer=True, heard=(), apps=None):
    asked, answers = [], list(heard)
    c = Context({}, apps or AppIndex([]), None, lambda t: None, lambda q: asked.append(q) or answer,
                hear=lambda q: asked.append(q) or (answers.pop(0) if answers else ""))
    return c, asked


@pytest.mark.parametrize("said, skill, args", [
    ("включи предыдущую вкладку", "switch_tab", {"name": "предыдущую"}),
    ("Включи следующую вкладку.", "switch_tab", {"name": "следующую"}),
    ("вернись на прошлую вкладку", "switch_tab", {"name": "прошлую"}),
    ("включи вторую вкладку", "switch_tab", {"name": "вторую"}),
    ("вкладка номер три", "switch_tab", {"name": "3"}),
    ("обнови браузер", "press_keys", {"action": "reload"}),
    ("Обнови страницу браузера.", "press_keys", {"action": "reload"}),
    ("перезагрузи вкладку", "press_keys", {"action": "reload"}),
    ("обнови эту страницу", "press_keys", {"action": "reload"}),
    ("В музыку.", "music", {"action": "play"}),
    ("включи музыку", "music", {"action": "play"}),
    ("поставь музыку", "music", {"action": "play"}),
    ("запусти приложение яндекс музыка", "music", {"action": "play"}),
    ("открой яндекс музыку", "music", {"action": "open"}),
    ("выключи музыку", "music", {"action": "stop"}),
    ("другое окно", "switch_window", {"name": "другое окно"}),
    ("Отправь сообщение.", "send_message", {"said": "Отправь сообщение.", "rest": ""}),
    ("напиши в телеграм маме, что я опоздаю", "send_message",
     {"said": "напиши в телеграм маме, что я опоздаю", "rest": "маме что я опоздаю"}),
])
def test_routes(fast, said, skill, args):
    plan = fast.match(said)
    assert plan is not None and plan.actions == [(skill, args)], plan


@pytest.mark.parametrize("said", ["отправь это", "отправь это в телеграм", "напиши привет", "обнови программу",
                                  "включи свет", "открой папку музыка", "пауза", "следующий трек"])
def test_not_taken(fast, said):
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


def test_previous_tab_is_ctrl_shift_tab():
    vk = winapi.VK
    for said in ("предыдущую", "включи предыдущую вкладку", "вкладку назад"):
        assert screen.tab_keys(said) == (vk["ctrl"], vk["shift"], vk["tab"])
    assert screen.tab_keys("вторую") == (vk["ctrl"], 0x32)


@pytest.mark.parametrize("text, cut", [("открой", True), ("напиши маме и", True), ("поставь таймер на", True),
                                       ("открой браузер", False), ("сделай громче", False), ("", False)])
def test_unfinished(text, cut):
    assert unfinished(text) is cut


def test_split_recipient():
    assert screen.split_recipient("ивану петровичу привет как дела", ["Иван Петрович", "Мама"]) == \
        ("ивану петровичу", "привет как дела")
    assert screen.split_recipient("маме буду в семь", []) == ("маме", "буду в семь")


def test_app_kind_word_and_music(tmp_path):
    (tmp_path / "Яндекс Музыка.lnk").write_text("")
    (tmp_path / "Telegram.lnk").write_text("")
    idx = AppIndex([tmp_path])
    assert idx.resolve("приложение яндекс музыка")[0] == "яндекс музыка"
    assert idx.music()[0] == "яндекс музыка"
    assert AppIndex([]).music() is None


# ——— музыка ———

@pytest.fixture
def player(monkeypatch, tmp_path):
    (tmp_path / "Яндекс Музыка.lnk").write_text("")
    st = {"sessions": [], "opened": [], "keys": [], "cmd": []}
    monkeypatch.setattr(winapi, "media_sessions", lambda: list(st["sessions"]))
    monkeypatch.setattr(winapi, "media_command", lambda app, c: st["cmd"].append((app, c)) or True)
    monkeypatch.setattr(winapi, "open_path", st["opened"].append)
    monkeypatch.setattr(winapi, "press", lambda *k: st["keys"].append(k))
    monkeypatch.setattr(winapi, "foreground_window", lambda: {"hwnd": len(st["opened"]), "exe": "x.exe"})
    monkeypatch.setattr(media.time, "sleep", lambda s: None)
    return st, AppIndex([tmp_path])


def test_music_already_playing(player):
    st, idx = player
    st["sessions"] = [{"app": "chrome", "status": "Playing"}]
    c, _ = ctx(apps=idx)
    assert REGISTRY["music"].handler(c, "play") == "Музыка уже играет." and st["opened"] == []


def test_music_paused_in_app_resumes_without_toggling(player):
    st, idx = player
    st["sessions"] = [{"app": "ru.yandex.desktop.music", "status": "Paused"}]
    c, _ = ctx(apps=idx)
    assert REGISTRY["music"].handler(c, "play") is None
    assert st["cmd"] == [("ru.yandex.desktop.music", "play")] and st["keys"] == [] and st["opened"] == []


def test_music_starts_app_and_presses_play_once(player):
    st, idx = player
    c, _ = ctx(apps=idx)
    assert REGISTRY["music"].handler(c, "play") is None
    assert st["opened"][0].endswith("Яндекс Музыка.lnk")
    assert st["keys"] == [(winapi.VK["media_play_pause"],)]


def test_music_without_app_opens_site(player):
    st, _ = player
    c, _ = ctx(apps=AppIndex([]))
    assert "браузере" in REGISTRY["music"].handler(c, "play") and st["opened"] == ["https://music.yandex.ru"]


def test_music_stop_only_pauses_playing(player):
    st, idx = player
    c, _ = ctx(apps=idx)
    assert REGISTRY["music"].handler(c, "stop") == "Музыка и так не играет."
    st["sessions"] = [{"app": "a", "status": "Playing"}, {"app": "b", "status": "Paused"}]
    REGISTRY["music"].handler(c, "stop")
    assert st["cmd"] == [("a", "pause")]


# ——— сообщения ———

class Messenger:
    """Telegram: список чатов, поле сообщения; что напечатано и нажато."""

    def __init__(self, monkeypatch, typed_already=""):
        self.typed, self.enter, self.opened = [], 0, []
        self.field = typed_already
        win = {"hwnd": 7, "pid": 1, "title": "Telegram", "exe": "Telegram.exe"}
        monkeypatch.setattr(winapi, "foreground_window", lambda: win)
        monkeypatch.setattr(winapi, "is_foreground", lambda h: True)
        monkeypatch.setattr(screen, "_telegram", lambda c: win)
        monkeypatch.setattr(uia, "controls", lambda hwnds, timeout=8: [
            Control("Мама 🌸, получено, Привет", "пункт списка"), Control("Работа, Отчёт", "пункт списка"),
            Control("Search", "поле"), Control("Написать сообщение…", "поле", value=self.field)])
        monkeypatch.setattr(uia, "press_control", lambda hwnds, name, kind: self.opened.append(name) or True)
        monkeypatch.setattr(winapi, "type_unicode", self.typed.append)
        monkeypatch.setattr(winapi, "press", self.key)
        monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    def key(self, *k):
        if k == (winapi.VK["enter"],):
            self.enter += 1


def test_send_all_in_one_phrase(monkeypatch):
    m = Messenger(monkeypatch)
    c, asked = ctx()
    said = "Напиши в телеграм маме, что я опоздаю на 5 минут!"
    assert REGISTRY["send_message"].handler(c, said, "маме что я опоздаю на 5 минут") == "Отправлено."
    assert m.opened == ["Мама 🌸, получено, Привет"] and m.typed == ["Я опоздаю на 5 минут!"]
    assert asked == ["Отправить — Мама 🌸: «Я опоздаю на 5 минут!»"] and m.enter == 1


def test_send_asks_who_and_what(monkeypatch):
    m = Messenger(monkeypatch)
    monkeypatch.setattr(winapi, "foreground_window", lambda: {"hwnd": 3, "pid": 2, "title": "Блокнот", "exe": "notepad.exe"})
    c, asked = ctx(heard=["маме", "Купи хлеба"])
    assert REGISTRY["send_message"].handler(c, "отправь сообщение", "") == "Отправлено."
    assert asked[:2] == ["Кому написать?", "Что написать?"] and m.typed == ["Купи хлеба"]


def test_send_no_answer_cancels(monkeypatch):
    Messenger(monkeypatch)
    monkeypatch.setattr(winapi, "foreground_window", lambda: {"hwnd": 3, "pid": 2, "title": "Блокнот", "exe": "notepad.exe"})
    c, _ = ctx(heard=[""])
    with pytest.raises(screen.Cancelled):
        REGISTRY["send_message"].handler(c, "отправь сообщение", "")


def test_send_already_typed_text(monkeypatch):
    m = Messenger(monkeypatch, typed_already="Буду в семь")
    c, asked = ctx()
    assert REGISTRY["send_message"].handler(c, "отправь сообщение", "") == "Отправлено."
    assert asked == ["Отправить «Буду в семь»"] and m.typed == [] and m.enter == 1


def test_send_declined_keeps_text(monkeypatch):
    m = Messenger(monkeypatch)
    c, _ = ctx(answer=False, heard=["Привет"])
    assert "не отправ" in REGISTRY["send_message"].handler(c, "отправь сообщение", "").lower()
    assert m.typed == ["Привет"] and m.enter == 0


# ——— окна-близнецы ———

@pytest.fixture
def windows(monkeypatch):
    wins = [{"hwnd": 1, "title": "YouTube — Google Chrome", "exe": "chrome.exe", "pid": 10, "active": True},
            {"hwnd": 2, "title": "Блокнот", "exe": "notepad.exe", "pid": 20, "active": False},
            {"hwnd": 3, "title": "Почта — Google Chrome", "exe": "chrome.exe", "pid": 11, "active": False},
            {"hwnd": 4, "title": "Новости — Google Chrome", "exe": "chrome.exe", "pid": 12, "active": False}]
    focused = []
    monkeypatch.setattr(winapi, "open_windows", lambda: wins)
    monkeypatch.setattr(winapi, "foreground_window", lambda: wins[0])
    monkeypatch.setattr(winapi, "focus_window", focused.append)
    import psutil

    class P:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name}
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [P(10, "chrome.exe"), P(11, "chrome.exe"),
                                                                     P(12, "chrome.exe"), P(20, "notepad.exe")])
    return focused


def test_window_by_title_words(windows):
    c, _ = ctx()
    assert REGISTRY["switch_window"].handler(c, "хром с почтой") is None and windows == [3]
    assert REGISTRY["switch_window"].handler(c, "окно браузера где новости") is None and windows[-1] == 4


def test_same_window_again_goes_to_next(windows):
    c, _ = ctx()
    reply = REGISTRY["switch_window"].handler(c, "хром")     # хром уже впереди — самое давнее окно хрома
    assert windows == [4] and "Таких окон 3" in reply and "другое окно" in reply


def test_other_window_of_same_program(windows):
    c, _ = ctx()
    assert REGISTRY["switch_window"].handler(c, "другое окно") == "«Новости»." and windows == [4]
