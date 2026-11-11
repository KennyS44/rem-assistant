"""Окна на рабочем столе: свернуть, развернуть на весь экран, вернуть, половина экрана, другой монитор.
Только быстрым путём (разбор фразы — _parse): модель этого умения не видит, её подсказка не растёт.

«Сверни телеграм», «разверни браузер на весь экран», «растяни окно», «верни окно как было»,
«поставь окно влево», «блокнот вправо», «перенеси окно на второй монитор». Без названия
(«окно», «это», «его») — окно, с которым сейчас работают."""
from __future__ import annotations

import re

from .. import winapi
from ..text import norm
from .apps import BROWSER_WORDS, find_windows
from .base import Param, skill

ACTIONS = ["minimize", "maximize", "restore", "left", "right", "monitor"]
SELF = {"", "окно", "окошко", "это", "это окно", "его", "ее", "текущее", "текущее окно", "активное окно",
        "открытое окно", "мое окно", "экран"}
BROWSERS = {"chrome", "msedge", "firefox", "opera", "browser", "brave", "vivaldi", "yandex"}
_SIDE = {"влево": "left", "налево": "left", "слева": "left", "левую": "left", "левой": "left",
         "вправо": "right", "направо": "right", "справа": "right", "правую": "right", "правой": "right"}
_MOVE = r"(?:поставь|перенеси|подвинь|сдвинь|прижми|прикрепи|закрепи|отправь|передвинь|переставь|перекинь|убери|кинь)"
# (действие, шаблон, только «окно/это»: «увеличь громкость» — не про окно)
_RULES = [
    ("monitor", re.compile(rf"^{_MOVE}\s+(?P<x>.*?)\s*на\s+(?:другой|второй|соседний|первый|левый|правый|"
                           r"следующий)\s+(?:монитор|экран|дисплей)$"), False),
    ("maximize", re.compile(r"^(?:(?:разверни|растяни|раскрой|сделай|открой|поставь)\s+)?(?P<x>.*?)\s*"
                            r"(?:на|во)\s+весь\s+экран$"), False),
    ("maximize", re.compile(r"^растяни\s*(?P<x>.*)$"), False),
    ("maximize", re.compile(r"^(?:разверни|раскрой|увеличь)\s+(?P<x>.*)$"), True),
    ("restore", re.compile(r"^(?:верни|восстанови|сделай)\s*(?P<x>.*?)\s*(?:как было|"
                           r"(?:в |до )?(?:обычн\w+|прежн\w+|нормальн\w+)\s+размер\w*)$"), False),
    ("restore", re.compile(r"^(?:уменьши|восстанови)\s*(?P<x>.*)$"), True),
    ("minimize", re.compile(r"^(?:сверни|спрячь)\s*(?P<x>.*)$"), False),
    ("side", re.compile(rf"^(?:{_MOVE}\s+)?(?P<x>.*?)\s*(?:в |на )?(?P<side>влево|налево|вправо|направо|слева|"
                        r"справа|левую|правую|левой|правой)(?:\s+(?:половин\w+|част\w+|сторон\w+))?"
                        r"(?:\s+экрана)?$"), False),
]


def _is_self(x: str) -> bool:
    return x in SELF or x.startswith("окно ")


def _parse(text: str):
    t = norm(text)
    for action, rx, self_only in _RULES:
        m = rx.match(t)
        if not m:
            continue
        x = m.group("x").strip()
        if x.startswith(("все", "всё")) or len(x.split()) > 4:     # «сверни все окна» — отдельное умение
            return None
        if self_only and x and not _is_self(x):
            return None
        if action == "side":
            if not re.match(_MOVE, t) and not _is_self(x):
                return None                              # «браузер вправо» — только с «окно» или глаголом
            action = _SIDE[m.group("side")]
        return {"action": action, "name": x}
    return None


def _target(ctx, name: str) -> dict | None:
    """Окно по словам из команды; «окно», «это», пусто — то, с которым работают."""
    q = norm(name).removeprefix("окно ").strip()
    if q in SELF:
        return winapi.foreground_window()
    if q in BROWSER_WORDS or q in ("браузера", "интернета"):
        wins = [w for w in winapi.open_windows() if w["exe"].lower().removesuffix(".exe") in BROWSERS]
        return wins[0] if wins else None
    for cand in (q, q[:-1] if len(q) > 4 and q[-1] in "аяуы" else ""):   # «окно телеграма» → «телеграм»
        if cand:
            hits = find_windows(ctx, cand)
            if hits:
                return hits[0]
    return None


@skill("window_control", "Окна: свернуть, развернуть, половина экрана",
       "Свернуть, развернуть на весь экран, вернуть обычный размер, поставить на левую/правую половину "
       "экрана или перенести на другой монитор.",
       params=[Param("action", "string", "что сделать", enum=ACTIONS),
               Param("name", "string", "программа или окно; пусто — активное", required=False)],
       category="Программы", model=False, parse=_parse)
def window_control(ctx, action: str, name: str = ""):
    w = _target(ctx, name)
    if w is None:
        return ctx.phrase(f"Окно «{name}» не найдено.", f"Простите, Рэм не нашла окно «{name}».")
    if action in ("left", "right"):
        winapi.window_half(w["hwnd"], action)
    elif action == "monitor":
        if winapi.monitor_count() < 2:
            return "Монитор один — переносить некуда."
        winapi.window_next_monitor(w["hwnd"])
    else:
        winapi.show_window(w["hwnd"], action)
    return None
