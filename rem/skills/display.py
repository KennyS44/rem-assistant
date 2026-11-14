"""Экран: ярче/темнее, яркость числом, ночной свет, погасить и включить экран.
Только быстрым путём (разбор фразы): модель этого умения не видит.

Яркость внешнего монитора меняется по DDC/CI (в меню монитора эта настройка должна быть включена),
экрана ноутбука — через WMI. Ночной свет — та же настройка, что в «Параметры → Дисплей»."""
from __future__ import annotations

import re

from .. import winapi
from ..text import norm, plural
from .base import Param, skill

STEP = 20
ACTIONS = ["up", "down", "set", "night_on", "night_off", "off", "on", "ask"]

_BRIGHT = r"(?:ярк\w*|подсветк\w*)"
_RULES = [
    ("up", re.compile(rf"^(?:(?:сделай|сделать|можно)\s+)?(?:экран\s+|монитор\s+)?(?:по)?ярче(?:\s+экран\w*|"
                      rf"\s+монитор\w*)?$|^(?:прибавь|добавь|увеличь|повысь|подними|выкрути)\s+{_BRIGHT}"
                      rf"(?:\s+экрана|\s+монитора)?$")),
    ("down", re.compile(rf"^(?:(?:сделай|сделать|можно)\s+)?(?:экран\s+|монитор\s+)?(?:по)?темнее(?:\s+экран\w*|"
                        rf"\s+монитор\w*)?$|^(?:убавь|уменьши|понизь|снизь|приглуши)\s+{_BRIGHT}"
                        rf"(?:\s+экрана|\s+монитора)?$")),
    ("max", re.compile(rf"^{_BRIGHT}(?:\s+экрана|\s+монитора)?\s+(?:на\s+)?(?:максимум|максимальн\w*|полн\w*)$|"
                       rf"^(?:сделай|поставь|выкрути)\s+{_BRIGHT}\s+(?:на\s+)?(?:максимум|полн\w*)$")),
    ("min", re.compile(rf"^{_BRIGHT}(?:\s+экрана|\s+монитора)?\s+(?:на\s+)?(?:минимум|минимальн\w*)$|"
                       rf"^(?:сделай|поставь)\s+{_BRIGHT}\s+(?:на\s+)?минимум$")),
    ("set", re.compile(rf"^(?:(?:поставь|сделай|установи)\s+)?{_BRIGHT}(?:\s+экрана|\s+монитора)?\s+"
                       r"(?:на\s+|в\s+)?(?P<n>\d{1,3})(?:\s*%|\s+процент\w*)?$")),
    ("ask", re.compile(rf"^(?:какая|сколько)\s+(?:сейчас\s+)?{_BRIGHT}(?:\s+экрана|\s+монитора)?$")),
    ("night_on", re.compile(r"^(?:включи|вруби|активируй|поставь)\s+ночн\w+\s+(?:свет|режим)\w*$|"
                            r"^ночн\w+\s+(?:свет|режим)\w*$")),
    ("night_off", re.compile(r"^(?:выключи|отключи|убери|выруби)\s+ночн\w+\s+(?:свет|режим)\w*$")),
    ("off", re.compile(r"^(?:выключи|отключи|погаси|потуши|выруби)\s+(?:экран|монитор|мониторы|экраны|дисплей)$")),
    ("on", re.compile(r"^(?:включи|разбуди|зажги)\s+(?:экран|монитор|мониторы|экраны|дисплей)$")),
]


def _parse(text: str):
    from ..fastpath import words_to_digits          # fastpath сам импортирует умения
    t = words_to_digits(norm(text))
    for action, rx in _RULES:
        m = rx.match(t)
        if not m:
            continue
        if action == "max":
            return {"action": "set", "level": 100}
        if action == "min":
            return {"action": "set", "level": 0}
        if action == "set":
            n = int(m.group("n"))
            return {"action": "set", "level": n} if n <= 100 else None
        return {"action": action}
    return None


def _pct(n: int) -> str:
    return f"{n} {plural(n, 'процент', 'процента', 'процентов')}"


def _no_brightness(ctx) -> str:
    return ctx.phrase("Яркость этого монитора не меняется из программ. Включите DDC/CI в меню монитора "
                      "(кнопки на самом мониторе) и скажите ещё раз.",
                      "Простите, Рэм не может менять яркость этого монитора. Включите DDC/CI в меню "
                      "монитора (кнопки на самом мониторе) — и Рэм попробует ещё раз.")


@skill("display", "Экран: яркость, ночной свет, выключить",
       "Сделать экран ярче или темнее, яркость числом, включить или выключить ночной свет, погасить экран.",
       params=[Param("action", "string", "что сделать", enum=ACTIONS),
               Param("level", "integer", "яркость в процентах", minimum=0, maximum=100, required=False)],
       category="Система", model=False, parse=_parse)
def display(ctx, action: str, level: int | None = None):
    if action in ("up", "down", "set", "ask"):
        if action == "set":
            got = winapi.brightness(set_to=int(level or 0))
        elif action == "ask":
            got = winapi.brightness()
        else:
            got = winapi.brightness(step=STEP if action == "up" else -STEP)
        if not got:
            return _no_brightness(ctx)
        return f"Яркость {_pct(got[0])}."
    if action in ("night_on", "night_off"):
        on = action == "night_on"
        if winapi.night_light(on) is None:
            winapi.open_path("ms-settings:nightlight")
            return ctx.phrase("Открыты настройки ночного света — переключатель вверху.",
                              "Рэм открыла настройки ночного света — переключатель вверху.")
        return "Ночной свет включён." if on else "Ночной свет выключен."
    if action == "off":
        winapi.monitor_off()
        return None
    winapi.monitor_on()
    return None
