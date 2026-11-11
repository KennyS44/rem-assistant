"""Окна (свернуть/развернуть/половина экрана), YouTube и лимит облака — без Windows и сети."""
import pytest

from rem import config as cfgmod
from rem.brain import Brain, OllamaError, is_quota_error
from rem.fastpath import FastPath
from rem.skills import REGISTRY, active_skills
from rem.skills.web import parse_first_video
from rem.skills.window import _parse


@pytest.fixture(scope="module")
def fast():
    return FastPath(active_skills(dict(cfgmod.DEFAULTS)))


@pytest.mark.parametrize("said, skill, args", [
    ("сверни телеграм", "window_control", {"action": "minimize", "name": "телеграм"}),
    ("сверни окно", "window_control", {"action": "minimize", "name": "окно"}),
    ("разверни браузер на весь экран", "window_control", {"action": "maximize", "name": "браузер"}),
    ("на весь экран", "window_control", {"action": "maximize", "name": ""}),
    ("растяни окно", "window_control", {"action": "maximize", "name": "окно"}),
    ("верни окно как было", "window_control", {"action": "restore", "name": "окно"}),
    ("поставь окно влево", "window_control", {"action": "left", "name": "окно"}),
    ("поставь браузер на правую половину экрана", "window_control", {"action": "right", "name": "браузер"}),
    ("перенеси окно на второй монитор", "window_control", {"action": "monitor", "name": "окно"}),
    ("сверни все окна", "minimize_all", {}),
    ("Рэм, включи на ютубе обзор RTX 5070", "youtube", {"query": "обзор rtx 5070", "play": "да"}),
    ("найди на ютубе рецепт борща", "youtube", {"query": "рецепт борща", "play": "нет"}),
    ("включи котиков на YouTube", "youtube", {"query": "котиков", "play": "да"}),
    ("громкость 30", "volume_set", {"level": 30}),
])
def test_fast_routes(fast, said, skill, args):
    plan = fast.match(said.removeprefix("Рэм, "))
    assert plan is not None and plan.actions == [(skill, args)]


@pytest.mark.parametrize("said", ["увеличь громкость", "уменьши звук", "открой браузер", "убери напоминания",
                                  "блокнот вправо", "включи видео", "открой ютуб", "найди котиков"])
def test_not_window_or_youtube(fast, said):
    plan = fast.match(said)
    assert plan is None or plan.actions[0][0] not in ("window_control", "youtube"), plan


def test_switch_window_still_wins():
    assert _parse("разверни телеграм") is None          # «разверни X» — показать окно (switch_window)


def test_parse_first_video():
    page = ('...{"adSlotRenderer":{}}...{"videoRenderer":{"videoId":"Ry4Yh6Qv6DY","thumbnail":{},'
            '"title":{"runs":[{"text":"RTX 5070 \\u2014 \\"обзор\\""}]}}...'
            '{"videoRenderer":{"videoId":"CafxnVgL7fQ","title":{"runs":[{"text":"второе"}]}}')
    assert parse_first_video(page) == ("https://www.youtube.com/watch?v=Ry4Yh6Qv6DY", 'RTX 5070 — "обзор"')
    assert parse_first_video("<html>consent</html>") is None


def test_youtube_falls_back_to_search(monkeypatch):
    from rem.assistant import Context
    from rem.skills import web
    from rem.skills.apps import AppIndex
    opened = []
    monkeypatch.setattr(web, "first_video", lambda q: None)
    monkeypatch.setattr(web.winapi, "open_path", opened.append)
    ctx = Context({}, AppIndex([]), None, lambda t: None)
    say = REGISTRY["youtube"].handler(ctx, query="котики", play="да")
    assert opened == ["https://www.youtube.com/results?search_query=%D0%BA%D0%BE%D1%82%D0%B8%D0%BA%D0%B8"]
    assert "поиск" in say


def test_window_control_targets(monkeypatch):
    from rem.assistant import Context
    from rem.skills import window
    from rem.skills.apps import AppIndex
    done = []
    fg = {"hwnd": 7, "title": "Блокнот", "exe": "notepad.exe", "pid": 1}
    monkeypatch.setattr(window.winapi, "foreground_window", lambda: fg)
    monkeypatch.setattr(window.winapi, "open_windows", lambda: [
        {"hwnd": 9, "title": "YouTube — Google Chrome", "exe": "chrome.exe", "pid": 2, "active": False}])
    monkeypatch.setattr(window.winapi, "show_window", lambda h, how: done.append((h, how)))
    monkeypatch.setattr(window.winapi, "window_half", lambda h, side: done.append((h, side)))
    monkeypatch.setattr(window.winapi, "monitor_count", lambda: 1)
    ctx = Context({}, AppIndex([]), None, lambda t: None)
    h = REGISTRY["window_control"].handler
    assert h(ctx, action="maximize", name="окно") is None
    assert h(ctx, action="left", name="браузер") is None
    assert h(ctx, action="minimize", name="окно браузера") is None
    assert done == [(7, "maximize"), (9, "left"), (9, "minimize")]
    assert "один" in h(ctx, action="monitor")


def test_cloud_quota_pauses_cloud():
    class Client:
        calls = []

        def chat_tools(self, model, *a, **kw):
            self.calls.append(model)
            if model.endswith(":cloud"):
                raise OllamaError('Ollama ответила 402: {"error":"Add usage credits or subscribe"}')
            return {"actions": [], "reply": "ок"}

    cfg = dict(cfgmod.DEFAULTS, cloud_model="deepseek-v4.1-flash:cloud")
    cl = Client()
    b = Brain(cfg, active_skills(cfg), cl)
    assert b.plan("привет").source == "llm"
    assert b.plan("привет").source == "llm"
    assert cl.calls.count("deepseek-v4.1-flash:cloud") == 1          # второй раз облако не спрашивали
    assert is_quota_error(b.cloud_error) and not is_quota_error("Ollama ответила 401: unauthorized")
