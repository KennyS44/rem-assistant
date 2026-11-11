"""Напоминания и будильник на время суток (разбор фразы и хранилище — rem/reminders.py).
Только быстрым путём: модель этих умений не видит — её подсказка не меняется."""
from __future__ import annotations

import datetime as dt
import re

from .base import Param, skill
from .. import reminders as R
from ..text import plural

CANCEL = re.compile(r"^(?:\w+ )?(отмени|удали|выключи|убери|сбрось|отключи|сотри)\w*\b.*\b(напоминани|будильник)")
LIST = re.compile(r"^(?:\w+ )?(какие|какой|когда|покажи|скажи|перечисли|что)\b.*\b(напоминани|будильник|напомнить)")


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е")).split())


def _parse_set(text: str):
    t = _key(text)
    if CANCEL.search(t) or LIST.search(t):
        return None
    w = R.parse(text)
    if w is not None:
        return {"at": w.at.isoformat(timespec="seconds"), "text": w.text,
                "kind": "alarm" if w.alarm else "remind", "relative": "да" if w.relative else "нет"}
    if R.ASK_VERB.match(t) and "через" not in t.split():
        return {"at": "", "text": "", "kind": "alarm" if "разбуди" in t else "remind", "relative": "нет"}
    return None


def _parse_cancel(text: str):
    t = _key(text)
    if not CANCEL.search(t):
        return None
    return {"kind": "alarm" if "будильник" in t and "напоминани" not in t else ""}


def _parse_list(text: str):
    return {} if LIST.search(_key(text)) else None


@skill("set_reminder", "Напоминание или будильник",
       "Напомнить в указанное время или разбудить.",
       params=[Param("at", "string", "когда, ISO"), Param("text", "string", "о чём", required=False),
               Param("kind", "string", "что", enum=["remind", "alarm"], required=False),
               Param("relative", "string", "время сказано как «через …»", enum=["да", "нет"], required=False)],
       category="Информация", model=False, parse=_parse_set)
def set_reminder(ctx, at: str, text: str = "", kind: str = "remind", relative: str = "нет"):
    if not at:
        return ("Во сколько разбудить? Скажите, например: «разбуди в семь утра»." if kind == "alarm" else
                "Когда напомнить? Скажите, например: «напомни в семь вечера купить хлеб».")
    when = dt.datetime.fromisoformat(at)
    now = dt.datetime.now()
    if when <= now:
        return f"{when.hour}:{when.minute:02d} уже прошло. Скажите другое время."
    if ctx.reminders is None:
        return "Напоминания сейчас недоступны."
    ctx.reminders.add(when, text, kind)
    said = R.say_when(when, now, relative == "да")
    if kind == "alarm":
        return ctx.phrase(f"Разбужу {said}.", f"Рэм разбудит вас {said}.")
    return ctx.phrase(f"Напомню {said}", f"Рэм напомнит {said}") + (f": {text}." if text else ".")


@skill("list_reminders", "Какие напоминания", "Сказать, какие напоминания и будильники поставлены.",
       examples=["какие напоминания", "какие у меня напоминания", "мои напоминания", "список напоминаний",
                 "какие будильники", "когда будильник", "на сколько будильник", "что я просил напомнить"],
       category="Информация", model=False, parse=_parse_list)
def list_reminders(ctx):
    items = ctx.reminders.upcoming() if ctx.reminders else []
    if not items:
        return "Напоминаний и будильников нет."
    now = dt.datetime.now()
    parts = []
    for x in items[:5]:
        when = R.say_when(dt.datetime.fromisoformat(x["at"]), now)
        what = "будильник" if x.get("kind") == "alarm" else (x.get("text") or "напоминание")
        parts.append(f"{when} — {what}")
    more = f" И ещё {len(items) - 5}." if len(items) > 5 else ""
    n = len(items)
    return f"{n} {plural(n, 'запись', 'записи', 'записей')}: " + "; ".join(parts) + "." + more


@skill("cancel_reminders", "Отменить напоминания", "Отменить напоминания или будильник.",
       params=[Param("kind", "string", "только будильники", enum=["alarm", ""], required=False)],
       examples=["отмени напоминания", "отмени все напоминания", "удали напоминания",
                 ("отмени будильник", {"kind": "alarm"}), ("выключи будильник", {"kind": "alarm"}),
                 ("убери будильник", {"kind": "alarm"})],
       category="Информация", model=False, parse=_parse_cancel)
def cancel_reminders(ctx, kind: str = ""):
    n = ctx.reminders.cancel_all(kind or None) if ctx.reminders else 0
    if kind == "alarm":
        return "Будильник отменён." if n else "Будильник не стоит."
    return "Напоминания отменены." if n else "Напоминаний нет."
