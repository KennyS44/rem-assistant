"""Кнопки и поля в открытых окнах: «нажми Сохранить», «напиши в поиск…», «что в окне».

Элементы окна берутся из Windows UI Automation (см. rem/uia.py). Опасные кнопки
(«Удалить», «Отправить», «Оплатить»…) Рэм нажимает только после голосового «да».
Когда нажатие выбрала модель, она видит окно после нажатия и решает, что дальше, —
так проходятся задачи в несколько нажатий («закрой блокнот без сохранения»).
"""
from __future__ import annotations

import time

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


def active_dialog(ctx, max_buttons: int = 12) -> str:
    """Кнопки активного окна, если это небольшое окно (диалог, мастер установки): модель сразу
    видит, что значит «подтверди» или «согласись». Большие окна (браузер, Word) — только по look_at_window."""
    w = winapi.foreground_window()
    if not w:
        return ""
    found = uia.controls([w["hwnd"]], timeout=1.5)
    if not found or sum(c.kind != "текст" for c in found) > max_buttons:
        return ""
    return "Активное о" + uia.describe(w["title"], w["exe"], found, limit=400)[1:]    # «Окно…» → «Активное окно…»


def after_click(ctx, name: str = "", window: str = "") -> str:
    """Что стало с окном после нажатия — модель решит, нужно ли нажать что-то ещё."""
    return "Теперь " + look(ctx, window)


@skill("click_button", "Нажать кнопку в окне",
       "Нажать в окне кнопку, пункт меню, вкладку или флажок по названию на экране. "
       "window — только если нужно другое, не активное окно.",
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


# здесь Enter отправляет сообщение людям — сначала спросить
MESSENGERS = ("telegram", "discord", "whatsapp", "viber", "skype", "slack", "teams", "zoom", "outlook",
              "thunderbird", "signal")


def _sends_to_people(w: dict, field: str) -> bool:
    exe = w.get("exe", "").lower()
    where = f"{exe} {w.get('title', '')} {field}".lower()
    return any(m in exe for m in MESSENGERS) or any(s in where for s in ("сообщ", "message", "чат", "chat",
                                                                         "почт", "mail"))


@skill("type_text", "Вписать текст в окно",
       "Вписать текст в поле окна: сообщение, поиск, адрес, имя файла. field — название поля, если полей "
       "несколько; без него — в единственное поле или туда, где курсор. enter=да — после текста нажать "
       "Enter (отправить, найти).",
       params=[Param("text", "string", "что вписать, как сказал пользователь"),
               Param("field", "string", "название поля", required=False),
               Param("enter", "string", "нажать Enter после текста", enum=["да", "нет"], required=False),
               Param("window", "string", "программа или заголовок окна", required=False)],
       category="Окна")
def type_text(ctx, text: str, field: str = "", enter: str = "нет", window: str = ""):
    try:
        w, hwnds = _target(ctx, window)
    except LookupError as e:
        raise NotHere(str(e))
    if not winapi.is_foreground(w["hwnd"]):
        winapi.focus_window(w["hwnd"])          # клавиатура печатает только в активное окно
        time.sleep(0.3)
    what, label = uia.focus_field(hwnds, field)
    if what == "missing":
        raise NotHere(f"Поля «{field}» в окне не видно.")
    winapi.type_unicode(text)
    if enter == "да":
        if _sends_to_people(w, label or field) and not ctx.confirm("Отправить"):
            raise Cancelled                       # текст остаётся в поле, не отправлен
        winapi.press(winapi.VK["enter"])
    return None
