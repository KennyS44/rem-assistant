"""Вкладки («следующая вкладка», «вкладка почта») и чаты Telegram («открой чат с мамой»);
после неудачного действия Рэм не говорит «готово»."""
from pathlib import Path

import pytest

from rem import config as cfgmod
from rem import uia, winapi
from rem.fastpath import FastPath
from rem.skills import REGISTRY, active_skills, screen
from rem.skills.apps import AppIndex
from rem.uia import Control

from .test_core import Steps
from .test_screen import FakeScreen, _assistant, _ctx


@pytest.mark.parametrize("said,name", [
    ("мамой", "Мама 🌸"), ("димой", "Дима"), ("петей", "Петя Иванов"), ("работой", "Работа"),
    ("почту", "Почта - Memory usage - 19.4 MB"), ("дима", "Dima"),
])
def test_names_in_any_case_match(said, name):
    assert screen.find_named(said, ["Максим", name, "Новости"]) == 1


def test_chat_name_strips_extra_words():
    assert screen.chat_name("с мамой в телеграме") == "мамой"
    assert screen.chat_name("чат со Светой") == "светой"
    assert screen.chat_name("Рабочий чат") == "рабочий чат"
    assert screen.chat_title("Мама, Получено, Привет!, в 12:30") == "Мама"


def test_fast_path_routes_chats_and_tabs(tmp_path):
    (tmp_path / "Telegram.lnk").touch()
    fp = FastPath(active_skills(cfgmod.DEFAULTS), AppIndex([Path(tmp_path)]))
    assert fp.match("открой чат с мамой").actions == [("open_chat", {"name": "с мамой"})]
    assert fp.match("открой телеграм маму").actions == [("open_chat", {"name": "маму"})]
    assert fp.match("открой телеграм").actions[0][0] == "open_app"
    assert fp.match("следующая вкладка").actions == [("switch_tab", {"name": "следующая вкладка"})]
    assert fp.match("переключись на вкладку почта").actions == [("switch_tab", {"name": "почта"})]
    assert fp.match("переключись на вкладку три").actions == [("switch_tab", {"name": "3"})]


def test_tab_keys():
    ctrl, shift, tab = winapi.VK["ctrl"], winapi.VK["shift"], winapi.VK["tab"]
    assert screen.tab_keys("следующую вкладку") == (ctrl, tab)
    assert screen.tab_keys("предыдущая") == (ctrl, shift, tab)
    assert screen.tab_keys("третью вкладку") == (ctrl, 0x33)
    assert screen.tab_keys("2") == (ctrl, 0x32)
    assert screen.tab_keys("последняя") == (ctrl, 0x39)
    assert screen.tab_keys("пятерочка") is None                 # это название вкладки, не номер


class FakeBrowser:
    """Окно с вкладками: заголовок окна — активная вкладка."""
    def __init__(self, monkeypatch, tabs=("Почта", "Погода", "Новости"), show_tabs=True):
        self.tabs, self.active, self.keys, self.pressed = list(tabs), 0, [], []
        monkeypatch.setattr(winapi, "foreground_window",
                            lambda: {"hwnd": 1, "pid": 1, "title": self.tabs[self.active] + " - Browser", "exe": "x.exe"})
        monkeypatch.setattr(uia, "controls", lambda hwnds, timeout=8:
                            [Control(t, "вкладка") for t in self.tabs] if show_tabs else [Control("Back", "кнопка")])
        monkeypatch.setattr(uia, "press_control", self.press)
        monkeypatch.setattr(winapi, "press", self.key)
        monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    def press(self, hwnds, name, kind):
        self.pressed.append(name)
        self.active = self.tabs.index(name)
        return True

    def key(self, *keys):
        self.keys.append(keys)
        if keys == (winapi.VK["ctrl"], winapi.VK["tab"]):
            self.active = (self.active + 1) % len(self.tabs)


def test_switch_tab_by_name(monkeypatch):
    b = FakeBrowser(monkeypatch)
    assert REGISTRY["switch_tab"].handler(_ctx(), name="вкладку новости") is None
    assert b.pressed == ["Новости"]
    assert REGISTRY["switch_tab"].handler(_ctx(), name="музыка") == "Вкладки «музыка» не видно."


def test_switch_tab_next_checks_title(monkeypatch):
    b = FakeBrowser(monkeypatch)
    assert REGISTRY["switch_tab"].handler(_ctx(), name="следующая") is None and b.active == 1
    b.tabs = ["Одна"]
    b.active = 0
    assert REGISTRY["switch_tab"].handler(_ctx(), name="следующая") == "Вкладка не переключилась."


def test_switch_tab_without_visible_tabs_cycles(monkeypatch):
    b = FakeBrowser(monkeypatch, show_tabs=False)               # Firefox: вкладки не видны, только заголовок
    assert REGISTRY["switch_tab"].handler(_ctx(), name="погоду") is None and b.active == 1
    assert REGISTRY["switch_tab"].handler(_ctx(), name="музыка") == "Вкладки «музыка» не нашла."
    assert b.active == 1                                        # прошли круг и вернулись


class FakeTelegram:
    def __init__(self, monkeypatch, chats=("Новости", "Мама 🌸", "Работа"), found_by_search=()):
        self.chats, self.search_hits, self.opened, self.typed, self.searching = list(chats), list(found_by_search), [], [], False
        monkeypatch.setattr(screen, "_telegram", lambda ctx: {"hwnd": 7, "title": "Telegram", "exe": "Telegram.exe"})
        monkeypatch.setattr(uia, "controls", self.controls)
        monkeypatch.setattr(uia, "focus_field", lambda hwnds, spoken="": ("focused", "Search"))
        monkeypatch.setattr(uia, "press_control", lambda hwnds, name, kind: self.opened.append(name) or True)
        monkeypatch.setattr(winapi, "press", lambda *k: None)
        monkeypatch.setattr(winapi, "type_unicode", self.type)
        monkeypatch.setattr(screen.time, "sleep", lambda s: None)

    def type(self, text):
        self.typed.append(text)
        self.searching = True

    def controls(self, hwnds, timeout=8):
        rows = self.search_hits if self.searching else self.chats
        return [Control("Search", "поле")] + [Control(f"{c}, получено, Привет, в 12:30", "пункт списка") for c in rows]


def test_open_visible_chat(monkeypatch):
    tg = FakeTelegram(monkeypatch)
    assert REGISTRY["open_chat"].handler(_ctx(), name="с мамой в телеграме") is None
    assert tg.opened == ["Мама 🌸, получено, Привет, в 12:30"] and tg.typed == []


def test_open_chat_through_search(monkeypatch):
    tg = FakeTelegram(monkeypatch, found_by_search=["Дима Петров"])
    assert REGISTRY["open_chat"].handler(_ctx(), name="с димой") is None
    assert tg.typed == ["дим"] and tg.opened == ["Дима Петров, получено, Привет, в 12:30"]


def test_missing_chat_is_reported_not_claimed(monkeypatch):
    tg = FakeTelegram(monkeypatch)
    assert REGISTRY["open_chat"].handler(_ctx(), name="с бабушкой") == "Чат «бабушкой» в Telegram не нашла."
    assert tg.opened == []


def test_no_done_after_failed_step(tmp_path, monkeypatch):
    """Кнопки нет, а модель отвечает «Готово» — Рэм говорит, что не вышло."""
    FakeScreen(monkeypatch)
    client = Steps({"actions": [{"skill": "click_button", "args": {"name": "Отправить"}}], "reply": ""},
                   {"actions": [], "reply": "Готово, сообщение отправлено."})
    a = _assistant(client, tmp_path)
    said = []
    a.say = said.append
    a.handle("отправь сообщение")
    assert said == ["Кнопки «Отправить» в окне не видно."]


def test_real_explanation_after_failure_is_kept(tmp_path, monkeypatch):
    FakeScreen(monkeypatch)
    client = Steps({"actions": [{"skill": "click_button", "args": {"name": "Отправить"}}], "reply": ""},
                   {"actions": [], "reply": "В этом окне нет такой кнопки, есть только «Сохранить»."})
    a = _assistant(client, tmp_path)
    said = []
    a.say = said.append
    a.handle("отправь сообщение")
    assert said == ["В этом окне нет такой кнопки, есть только «Сохранить»."]
