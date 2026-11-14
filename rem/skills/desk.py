"""Прочее: выключить компьютер через время, раскладка клавиатуры, «не беспокоить».
Только быстрым путём (разбор фразы): модель этих умений не видит.

«Выключи компьютер через час» / «в 23:30» — после «да» Windows сама выключит компьютер
(и покажет уведомление); «отмени выключение» — отменить. «Не беспокоить» — это
«Фокусировка внимания»: уведомления не всплывают, будильники Рэм звучат как обычно."""
from __future__ import annotations

import datetime as dt
import re

from .. import winapi
from ..text import norm
from .base import Param, skill

# ——— выключение по таймеру ———

_PC = r"(?:компьютер\w*|комп\w*|пк|ноутбук\w*|ноут)"
_SHUT = re.compile(rf"^(?:выключи|выключить|выруби|отключи|заверши работу)\s+(?:{_PC}\s+)?(?P<when>(?:через|в|во)\s+.+)$")
_CANCEL = re.compile(rf"^(?:отмени|отменить|отбой|стоп)\s+(?:выключени\w+|завершени\w+ работы)"
                     rf"(?:\s+{_PC})?$|^не\s+выключай(?:\s+{_PC})?$")


def _parse_shutdown(text: str):
    t = norm(text)
    if _CANCEL.match(t):
        return {"minutes": 0}
    m = _SHUT.match(t)
    if not m:
        return None
    from ..reminders import parse as parse_when
    now = dt.datetime.now()
    w = parse_when("напомни " + m.group("when"), now)
    if w is None or w.text.strip(" .") or w.at <= now:   # лишние слова — не уверены, пусть решает модель
        return None
    minutes = max(1, round((w.at - now).total_seconds() / 60))
    return {"minutes": minutes} if minutes <= 24 * 60 else None


@skill("shutdown_timer", "Выключить компьютер через время",
       "Выключить компьютер через сколько-то минут или в назначенное время; 0 — отменить.",
       params=[Param("minutes", "integer", "через сколько минут; 0 — отменить", minimum=0, maximum=24 * 60)],
       category="Питание", model=False, parse=_parse_shutdown)
def shutdown_timer(ctx, minutes: int):
    from ..reminders import say_when
    if not minutes:
        if winapi.shutdown_cancel():
            return "Выключение отменено."
        return "Выключение не было запланировано."
    at = dt.datetime.now() + dt.timedelta(minutes=minutes)
    when = say_when(at, relative=True) if minutes <= 180 else say_when(at)
    if not ctx.confirm(f"Выключить компьютер {when}"):
        return ctx.phrase("Хорошо, не выключаю.", "Хорошо, Рэм не будет.")
    if not winapi.shutdown_in(minutes * 60):
        winapi.shutdown_cancel()                      # было запланировано другое время — ставим новое
        if not winapi.shutdown_in(minutes * 60):
            return "Не получилось запланировать выключение."
    return f"Компьютер выключится {when}. Чтобы передумать, скажите «отмени выключение»."


# ——— раскладка ———

LANG_WORDS = {"англ": "en", "русс": "ru", "укр": "uk", "немец": "de", "француз": "fr", "испан": "es",
              "белорус": "be", "казах": "kk"}
LANG_NAME = {0x09: "английская", 0x19: "русская", 0x22: "украинская", 0x07: "немецкая", 0x0C: "французская",
             0x0A: "испанская", 0x23: "белорусская", 0x3F: "казахская"}
_LANG = r"(?P<lang>англ\w*|русс?к\w*|укр\w*|немец\w*|француз\w*|испан\w*|белорус\w*|казах\w*)"
_LAYOUT = re.compile(r"^(?:переключи|смени|поменяй|измени)\s+(?:раскладк\w*|язык(?:\s+ввода|\s+клавиатуры)?)$|"
                     r"^(?:другая|следующая)\s+раскладка$")
_LAYOUT_TO = [re.compile(r) for r in (
    rf"^(?:(?:переключи|смени|поменяй|поставь|сделай|включи)\s+)?(?:раскладку\s+|язык\s+)?(?:на\s+)?{_LANG}\s+"
    rf"(?:раскладк\w*|язык(?:\s+ввода)?)$",
    rf"^(?:переключи|смени|поменяй)\s+(?:раскладку|язык(?:\s+ввода)?)\s+на\s+{_LANG}(?:\s+язык)?$",
    rf"^(?:переключи|переключись)\s+на\s+{_LANG}(?:\s+раскладк\w*)?$")]


def _parse_layout(text: str):
    t = norm(text)
    if _LAYOUT.match(t):
        return {"lang": ""}
    m = next((m for rx in _LAYOUT_TO if (m := rx.match(t))), None)
    if not m:
        return None
    lang = next((code for stem, code in LANG_WORDS.items() if m.group("lang").startswith(stem)), None)
    return {"lang": lang} if lang else None


@skill("keyboard_layout", "Раскладка клавиатуры",
       "Переключить раскладку клавиатуры: следующую или конкретный язык.",
       params=[Param("lang", "string", "язык; пусто — следующая раскладка", enum=["", *winapi.LANGS],
                     required=False)],
       category="Окна", model=False, parse=_parse_layout)
def keyboard_layout(ctx, lang: str = ""):
    import time
    w = winapi.foreground_window()
    if w is None:
        return "Нет окна, где менять раскладку."
    hkl = None
    if lang:
        want = winapi.LANGS[lang]
        hkl = next((h for h in winapi.keyboard_layouts() if h & 0xFF == want), None)
        if hkl is None:
            return f"Раскладки «{LANG_NAME.get(want, lang)}» нет в Windows — её добавляют в параметрах языка."
        if winapi.current_layout(w["hwnd"]) == want:
            return f"Раскладка уже {LANG_NAME.get(want, lang)}."
    winapi.switch_layout(w["hwnd"], hkl)
    time.sleep(0.2)
    now = winapi.current_layout(w["hwnd"])
    return f"Раскладка {LANG_NAME[now]}." if now in LANG_NAME else None


# ——— не беспокоить ———

_QUIET_ON = re.compile(r"^(?:включи|поставь|активируй|вруби)\s+(?:режим\s+)?(?:не\s+беспокоить|"
                       r"фокусировк\w+(?:\s+внимания)?|тихий режим)$|^не\s+беспокоить$|"
                       r"^(?:не\s+беспокой\s+меня|без\s+уведомлений|отключи\s+уведомления|выключи\s+уведомления)$")
_QUIET_OFF = re.compile(r"^(?:выключи|отключи|убери|сними)\s+(?:режим\s+)?(?:не\s+беспокоить|"
                        r"фокусировк\w+(?:\s+внимания)?|тихий режим)$|"
                        r"^(?:включи|верни)\s+уведомления$|^можно\s+беспокоить$")


def _parse_quiet(text: str):
    t = norm(text)
    if _QUIET_OFF.match(t):
        return {"on": "off"}
    if _QUIET_ON.match(t):
        return {"on": "on"}
    return None


@skill("do_not_disturb", "Не беспокоить",
       "Включить или выключить режим «Не беспокоить» (фокусировка внимания): уведомления не всплывают.",
       params=[Param("on", "string", "включить или выключить", enum=["on", "off"])],
       category="Система", model=False, parse=_parse_quiet)
def do_not_disturb(ctx, on: str):
    want = 1 if on == "on" else 0
    if winapi.focus_assist(want) != want:
        winapi.open_path("ms-settings:quiethours")
        return ctx.phrase("Открыты настройки «Не беспокоить» — переключатель там.",
                          "Рэм открыла настройки «Не беспокоить» — переключатель там.")
    return "Режим «Не беспокоить» включён." if want else "Режим «Не беспокоить» выключен."
