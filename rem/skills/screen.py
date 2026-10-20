"""Кнопки в открытых окнах: «нажми Сохранить», «что в окне».

Элементы окна берутся из Windows UI Automation (см. rem/uia.py). Опасные кнопки
(«Удалить», «Отправить», «Оплатить»…) Рэм нажимает только после голосового «да».
Когда нажатие выбрала модель, она видит окно после нажатия и решает, что дальше, —
так проходятся задачи в несколько нажатий («закрой блокнот без сохранения»).
"""
from __future__ import annotations

from .. import uia, winapi
from .apps import find_windows
from .base import Cancelled, NotHere, Param, skill


def _target(ctx, window: str = "") -> tuple[dict, list[int]]:
    """Окно, о котором речь (по названию или активное), и все видимые окна той же программы —
    в них бывают диалоги и раскрытые меню. Своё окно первым."""
    if window:
        hits = find_windows(ctx, window)
        if not hits:
            raise LookupError(f"Окно «{window}» не найдено.")
        w = hits[0]
    else:
        w = winapi.foreground_window()
        if not w:
            raise LookupError("Нет открытых окон.")
    others = [h for h in winapi.windows_of_pids({w["pid"]}) if h != w["hwnd"]]
    return w, [w["hwnd"]] + others[:5]


def look(ctx, window: str = "") -> str:
    try:
        w, hwnds = _target(ctx, window)
    except LookupError as e:
        return str(e)
    return uia.describe(w["title"], w["exe"], uia.controls(hwnds))


def after_click(ctx, name: str = "", window: str = "") -> str:
    """Что стало с окном после нажатия — модель решит, нужно ли нажать что-то ещё."""
    return "Теперь " + look(ctx, window)


@skill("click_button", "Нажать кнопку в окне",
       "Нажать в окне кнопку, пункт меню, вкладку или флажок по названию на экране. "
       "window — если окно не активное.",
       params=[Param("name", "string", "название на экране"),
               Param("window", "string", "программа или заголовок окна", required=False)],
       examples=["нажми {label}", "нажми на {label}", "нажми кнопку {label}", "нажми на кнопку {label}",
                 "кликни {label}", "кликни на {label}", "щелкни {label}", "щелкни на {label}",
                 "жми {label}"],
       category="Окна", observe=after_click)
def click_button(ctx, name: str, window: str = ""):
    try:
        _, hwnds = _target(ctx, window)
    except LookupError as e:
        raise NotHere(str(e))
    what, label = uia.find_and_press(hwnds, name, ask=lambda n: ctx.confirm(f"Нажать «{n}»"))
    if what == "missing":
        raise NotHere(f"Кнопки «{name}» в окне не видно.")
    if what == "disabled":
        return f"Кнопка «{label}» сейчас неактивна."
    if what == "cancelled":
        raise Cancelled
    return None


@skill("look_at_window", "Что в окне",
       "Посмотреть текст и кнопки окна — перед нажатиями, когда задача в окне в несколько шагов.",
       params=[Param("window", "string", "программа или заголовок окна", required=False)],
       examples=["что в окне", "что можно нажать", "какие тут кнопки", "какие есть кнопки",
                 "что на экране", "прочитай окно"],
       category="Окна", retell=True)
def look_at_window(ctx, window: str = ""):
    return look(ctx, window)
