"""Напоминания и будильник на время суток: «напомни в семь вечера купить хлеб», «разбуди в 7:30».

Таймеры живут только в памяти; напоминания пишутся в %APPDATA%\\Rem\\reminders.json и
переживают перезапуск. Если компьютер был выключен, когда напоминание наступило, Рэм скажет
о нём при запуске (если прошло меньше суток).

Время разбирается без модели (parse) — это быстрый путь, модель про напоминания не знает
(её подсказка почти упирается в окно 4096 токенов). Понимаются:
  «в 19:30», «в 7 вечера», «в 8 утра», «в 7 часов 15 минут», «в половине восьмого»,
  «без четверти 8», «в четверть девятого», «в полдень», «в полночь», «к семи»;
  «через 20 минут», «через полчаса», «через час», «через 2 часа 15 минут»;
  «сегодня», «завтра», «послезавтра», «в пятницу», «12 ноября»; «завтра утром» — 9:00.
Час без «утра/вечера» — ближайший такой: «в 7» днём — это 19:00; будильник — утро.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import uuid
from pathlib import Path

log = logging.getLogger("rem.reminders")

MISSED_KEEP = dt.timedelta(hours=24)       # пропущенное (компьютер был выключен) — сказать при запуске
LATE_OK = dt.timedelta(minutes=10)         # опоздали меньше (сон компьютера, загрузка) — как вовремя

_CARD = {"один": 1, "одного": 1, "час": 1, "два": 2, "двух": 2, "три": 3, "трех": 3, "четыре": 4,
         "четырех": 4, "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8,
         "восьми": 8, "девять": 9, "девяти": 9, "десять": 10, "десяти": 10, "одиннадцать": 11,
         "одиннадцати": 11, "двенадцать": 12, "двенадцати": 12}
# «в половине восьмого» — порядковое в родительном: следующий час
_ORD = {"первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5, "шестого": 6,
        "седьмого": 7, "восьмого": 8, "девятого": 9, "десятого": 10, "одиннадцатого": 11,
        "двенадцатого": 12}
_MIN = {"пяти": 5, "десяти": 10, "пятнадцати": 15, "двадцати": 20, "двадцати пяти": 25,
        "четверти": 15, "5": 5, "10": 10, "15": 15, "20": 20, "25": 25}
WEEKDAYS_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]
PART_DEFAULT = {"утром": 9, "днем": 13, "вечером": 19, "ночью": 23}

TRIGGERS = re.compile(r"\b(напомни\w*|напоминани\w*|разбуди\w*|будильник\w*|подними)\b")
ALARM = re.compile(r"\b(разбуди\w*|будильник\w*|подними)\b")
FILLER = {"мне", "меня", "пожалуйста", "рэм", "рем", "поставь", "поставить", "установи", "сделай",
          "заведи", "создай", "на", "в", "к", "и", "а", "ну", "давай", "плиз", "сегодня"}


def _norm(text: str) -> str:
    """Нижний регистр, ё→е, числа словами → цифрами; «:» и «.» между цифрами сохраняются как «:»."""
    from .fastpath import words_to_digits
    t = text.lower().replace("ё", "е")
    t = re.sub(r"(\d{1,2})[:.](\d\d)\b", r"\1:\2", t)
    t = re.sub(r"[^\w\s:]", " ", t)
    t = re.sub(r"(?<!\d):|:(?!\d)", " ", t)
    t = words_to_digits(" ".join(t.split()))
    t = re.sub(r"\bпол\s?(?=" + "|".join(_ORD) + r")", "половине ", t)        # «полвосьмого»
    return " ".join(t.split())


def _num(w: str) -> int | None:
    if w.isdigit():
        return int(w)
    return _CARD.get(w)


class When:
    """Результат разбора: когда, что сказать и что осталось от фразы."""

    def __init__(self, at: dt.datetime, text: str, alarm: bool, relative: bool):
        self.at, self.text, self.alarm, self.relative = at, text, alarm, relative

    def __repr__(self):
        return f"When({self.at:%Y-%m-%d %H:%M}, {self.text!r}, alarm={self.alarm})"


ASK_VERB = re.compile(r"^(?:\w+ )?(напомни|разбуди)\b")


def parse(text: str, now: dt.datetime | None = None) -> When | None:
    """«напомни завтра в 7 вечера купить хлеб» → When(завтра 19:00, «купить хлеб»).
    None — это не напоминание или время не понятно (тогда пусть решает модель)."""
    exact = (now or dt.datetime.now()).replace(microsecond=0)     # «через 20 минут» — с секундами
    now = exact.replace(second=0)
    t = _norm(text)
    if not TRIGGERS.search(t):
        return None
    alarm = bool(ALARM.search(t))
    used: list[tuple[int, int]] = []           # куски фразы, ушедшие на время

    def take(m: re.Match) -> re.Match:
        used.append(m.span())
        return m

    # ——— через сколько ———
    rel = None
    m = re.search(r"\bчерез (полчаса|пол часа|полтора часа|час|(\d+|\w+) (минут\w*|час\w*)"
                  r"(?: (\d+) минут\w*)?)", t)
    if m:
        take(m)
        if m[1] in ("полчаса", "пол часа"):
            rel = dt.timedelta(minutes=30)
        elif m[1] == "полтора часа":
            rel = dt.timedelta(minutes=90)
        elif m[1] == "час":
            rel = dt.timedelta(hours=1)
        else:
            n = _num(m[2])
            if n is None:
                return None
            rel = dt.timedelta(minutes=n) if m[3].startswith("минут") else dt.timedelta(hours=n)
            if m[4]:
                rel += dt.timedelta(minutes=int(m[4]))
        if rel <= dt.timedelta(0) or rel > dt.timedelta(days=7):
            return None
        return When(exact + rel, _rest(t, used), alarm, True)

    # ——— день ———
    day, explicit_day = now.date(), False
    for word, add in (("послезавтра", 2), ("завтра", 1), ("сегодня", 0)):
        m = re.search(rf"\b{word}\b", t)
        if m:
            take(m)
            day, explicit_day = now.date() + dt.timedelta(days=add), True
            break
    else:
        m = re.search(r"\b(?:в|во) (" + "|".join(WEEKDAYS_ACC) + r")\b", t)
        if m:
            take(m)
            ahead = (WEEKDAYS_ACC.index(m[1]) - now.weekday()) % 7 or 7
            day, explicit_day = now.date() + dt.timedelta(days=ahead), True
        else:
            m = re.search(r"\b(\d{1,2}) (" + "|".join(MONTHS_GEN) + r")\b", t)
            if m:
                take(m)
                try:
                    day = dt.date(now.year, MONTHS_GEN.index(m[2]) + 1, int(m[1]))
                except ValueError:
                    return None
                if day < now.date():
                    day = day.replace(year=now.year + 1)
                explicit_day = True

    # ——— время ———
    hour = minute = None
    part = None                                       # утра | дня | вечера | ночи
    patterns = [
        (r"\b(?:в |к )?полдень\b", lambda m: (12, 0, "дня")),
        (r"\b(?:в |к )?полночь\b", lambda m: (0, 0, "ночи")),
        (r"\b(?:в |к )?половин[еау] (" + "|".join(_ORD) + r")\b", lambda m: (_ORD[m[1]] - 1, 30, None)),
        (r"\b(?:в |к )?четверть (" + "|".join(_ORD) + r")\b", lambda m: (_ORD[m[1]] - 1, 15, None)),
        (r"\b(?:в |к )?без (двадцати пяти|" + "|".join(k for k in _MIN if " " not in k)
         + r")(?: минут)? (\d{1,2}|\w+)\b",
         lambda m: (_num(m[2]) - 1 if _num(m[2]) else None, 60 - _MIN[m[1]], None)),
        (r"\b(?:в|к|на) (\d{1,2}):(\d\d)\b", lambda m: (int(m[1]), int(m[2]), None)),
        (r"\b(?:в|к|на) (\d{1,2}) час\w*(?: (\d{1,2}) минут\w*)?", lambda m: (int(m[1]), int(m[2] or 0), None)),
        (r"\b(?:в|к|на) (\d{1,2}) (\d\d)\b(?! минут| час)", lambda m: (int(m[1]), int(m[2]), None)),
        (r"\b(?:в|к|на) (\d{1,2}|" + "|".join(_CARD) + r")\b(?! минут| час| (?:" + "|".join(MONTHS_GEN) + "))",
         lambda m: (_num(m[1]), 0, None)),
    ]
    for rx, get in patterns:
        m = re.search(rx, t)
        if m:
            hour, minute, part = get(m)
            if hour is None:
                return None
            take(m)
            break
    pm = re.search(r"\b(утра|дня|вечера|ночи)\b", t)
    if pm and hour is not None:
        take(pm)
        part = part or pm[1]
    if hour is None:
        pd = re.search(r"\b(утром|днем|вечером|ночью)\b", t)
        if not pd:
            return None                               # «напомни купить хлеб» — когда? пусть решает модель
        take(pd)
        hour, minute, part = PART_DEFAULT[pd[1]], 0, "set"
    if hour == 24:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    if part in ("дня", "вечера") and hour < 12:
        hour += 12
    elif part == "ночи" and hour == 12:
        hour = 0
    elif part == "ночи" and 6 <= hour <= 11:
        hour += 12                                    # «в 11 ночи» — 23:00
    elif part == "утра" and hour == 12:
        hour = 0

    at = dt.datetime.combine(day, dt.time(hour, minute))
    if part is None and 1 <= hour <= 12:
        # час без «утра/вечера»: будильник — утро; иначе ближайший (днём «в 7» — это 19:00),
        # но не глубокой ночью: «завтра в 3» — 15:00
        evening = at + dt.timedelta(hours=12) if hour < 12 else at
        if alarm:
            pass
        elif explicit_day:
            at = evening if hour <= 6 else at
        elif at <= now or hour <= 5:
            at = evening
    if at <= now:
        if explicit_day:
            return When(at, _rest(t, used), alarm, False)   # «сегодня в 7 утра», а уже вечер — скажем
        at += dt.timedelta(days=1)
    return When(at, _rest(t, used), alarm, False)


def _rest(t: str, used: list[tuple[int, int]]) -> str:
    """Что осталось от фразы, кроме слов-поводов и времени: «купить хлеб»."""
    chars = list(t)
    for a, b in used:
        for i in range(a, b):
            chars[i] = " "
    words = TRIGGERS.sub(" ", "".join(chars)).split()
    while words and words[0] in FILLER:
        words.pop(0)
    for lead in (["о", "том", "что"], ["о", "том", "чтобы"], ["что"], ["чтобы"]):
        if words[:len(lead)] == lead:
            words = words[len(lead):]
            break
    while words and words[-1] in FILLER:
        words.pop()
    words = [w for w in words if w not in {"рэм", "рем", "пожалуйста"}]
    return " ".join(words)


# ——— как сказать время ———

def say_when(at: dt.datetime, now: dt.datetime | None = None, relative: bool = False) -> str:
    """«сегодня в 19:00», «завтра в 7:30», «в пятницу в 9:00», «12 ноября в 10:00», «через 20 минут»."""
    from .text import duration_ru
    now = now or dt.datetime.now()
    if relative:
        secs = int(round((at - now).total_seconds() / 60)) * 60
        return "через " + duration_ru(max(60, secs))
    days = (at.date() - now.date()).days
    clock = f"{at.hour}:{at.minute:02d}"
    if days == 0:
        return f"сегодня в {clock}"
    if days == 1:
        return f"завтра в {clock}"
    if days == 2:
        return f"послезавтра в {clock}"
    if days < 7:
        prep = "во" if WEEKDAYS_ACC[at.weekday()] == "вторник" else "в"
        return f"{prep} {WEEKDAYS_ACC[at.weekday()]} в {clock}"
    return f"{at.day} {MONTHS_GEN[at.month - 1]} в {clock}"


def due_phrase(item: dict, rem: bool = False) -> str:
    """rem — «в стиле Рэм»: о себе в третьем лице («Рэм напоминает»)."""
    text = item.get("text", "")
    if item.get("kind") == "alarm":
        at = dt.datetime.fromisoformat(item["at"])
        morning = 4 <= at.hour < 12
        first = "Доброе утро! Уже" if morning else "Будильник! Уже"
        wake = ", Рэм будит вас" if rem and morning else ""
        return f"{first} {at.hour}:{at.minute:02d}{wake}." + (f" {text[:1].upper()}{text[1:]}." if text else "")
    verb = "Рэм напоминает" if rem else "Напоминаю"
    if not text:
        return f"{verb}!" if not rem else "Рэм напоминает вам."
    if text.startswith(("про ", "о ", "об ")):
        return f"{verb} {text}."
    return f"{verb}: {text}."


# ——— хранилище и часы ———

class Reminders:
    """Список напоминаний на диске. check() — раз в несколько секунд из своего потока (start)."""

    def __init__(self, path: Path, on_due):
        self.path = path
        self.on_due = on_due                    # on_due(item, missed: bool)
        self.lock = threading.Lock()
        self.items: list[dict] = []
        self._stop = threading.Event()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.items = [x for x in data if isinstance(x, dict) and "at" in x]
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("напоминания не прочитались: %s", e)

    def add(self, at: dt.datetime, text: str, kind: str = "remind") -> dict:
        item = {"id": uuid.uuid4().hex[:8], "at": at.isoformat(timespec="seconds"), "text": text, "kind": kind}
        with self.lock:
            self.items.append(item)
            self.items.sort(key=lambda x: x["at"])
        self._save()
        log.info("напоминание: %s", item)
        return item

    def upcoming(self) -> list[dict]:
        with self.lock:
            return list(self.items)

    def cancel_all(self, kind: str | None = None) -> int:
        with self.lock:
            keep = [x for x in self.items if kind is not None and x.get("kind", "remind") != kind]
            n = len(self.items) - len(keep)
            self.items = keep
        self._save()
        return n

    def check(self, now: dt.datetime | None = None) -> list[dict]:
        """Наступившие — убрать из списка и сообщить. → что сработало."""
        now = now or dt.datetime.now()
        with self.lock:
            due = [x for x in self.items if dt.datetime.fromisoformat(x["at"]) <= now]
            self.items = [x for x in self.items if x not in due]
        if due:
            self._save()
        for x in due:
            late = now - dt.datetime.fromisoformat(x["at"])
            if late > MISSED_KEEP:
                log.info("пропущено давно, молчу: %s", x)
                continue
            try:
                self.on_due(x, late > LATE_OK)
            except Exception:
                log.exception("напоминание не прозвучало")
        return due

    def start(self, period: float = 5.0) -> None:
        def loop():
            while not self._stop.wait(period):
                self.check()
        self.check()                            # пропущенные, пока компьютер был выключен
        threading.Thread(target=loop, name="reminders", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _save(self) -> None:
        with self.lock:
            data = json.dumps(self.items, ensure_ascii=False, indent=1)
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("напоминания не сохранились: %s", e)
