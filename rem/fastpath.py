"""Быстрый путь: частые команды выполняются без языковой модели — мгновенно.

Нарочно строгий: срабатывает только при уверенном совпадении с примером умения.
Всё сомнительное уходит модели — лучше лишние полсекунды, чем не то действие.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .brain import Plan
from .skills import Skill
from .skills.apps import ALIASES, BROWSER_WORDS, SYSTEM_APPS
from .skills.web import SITES
from .text import norm

FILLER = {"пожалуйста", "плиз", "ка", "мне", "давай", "быстро", "срочно", "ну"}

_UNITS = {"ноль": 0, "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
          "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
          "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
          "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19}
_TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
         "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100}


def words_to_digits(text: str) -> str:
    """«громкость тридцать пять» → «громкость 35» (числа до 100)."""
    out, acc = [], None
    for w in text.split():
        if w in _TENS:
            if acc is not None:
                out.append(str(acc))
            acc = _TENS[w]
        elif w in _UNITS and (acc is None or (acc % 10 == 0 and acc < 100 and acc >= 20)):
            acc = (acc or 0) + _UNITS[w]
        else:
            if acc is not None:
                out.append(str(acc))
                acc = None
            out.append(w)
    if acc is not None:
        out.append(str(acc))
    return " ".join(out)


def clean(text: str) -> str:
    t = words_to_digits(norm(text))
    return " ".join(w for w in t.split() if w not in FILLER)


def _template_regex(example: str) -> re.Pattern:
    """«громкость {n}» → регулярка, допускающая «на 30», «30 процентов», «30%»."""
    rx = ""
    for p in re.split(r"(\{\w+\})", example):     # сначала слоты, потом нормализация текста
        if p == "{n}":
            rx += r" ?(?:на |до )?(?P<n>\d{1,3})(?: процент\w*| ?%)?"
        elif p in ("{app}", "{site}", "{q}"):
            rx += r" ?(?P<x>.+)"
        elif norm(p):
            rx += (" " if rx else "") + re.escape(norm(p))
    return re.compile("^" + rx.strip() + "$")


class FastPath:
    def __init__(self, skills: list[Skill], apps=None):
        self.apps = apps
        self.exact: dict[str, tuple[str, dict]] = {}    # фраза → (умение, аргументы)
        self.templates: list[tuple[re.Pattern, Skill, str]] = []
        ambiguous = set()
        for s in skills:
            needs_args = any(p.required for p in s.params)
            for ex in s.examples:
                ex, args = ex if isinstance(ex, tuple) else (ex, {})
                if "{" in ex:
                    self.templates.append((_template_regex(ex), s, ex))
                    continue
                if needs_args and not args:
                    continue                       # без аргументов такое умение не выполнить
                key = clean(ex)
                if key in self.exact and self.exact[key] != (s.name, args):
                    ambiguous.add(key)
                self.exact[key] = (s.name, args)
        for key in ambiguous:                      # одна фраза у двух умений — пусть решает модель
            del self.exact[key]
        self.skills = {s.name: s for s in skills}

    def match(self, text: str) -> Plan | None:
        t = clean(text)
        if not t:
            return None
        if t in self.exact:
            name, args = self.exact[t]
            return Plan(actions=[(name, dict(args))], source="fast")

        # почти точное совпадение: опечатка распознавания, но первое слово то же
        first = t.split()[0]
        best, best_score = None, 0.0
        for phrase, action in self.exact.items():
            if phrase.split()[0] != first:
                continue
            score = fuzz.ratio(t, phrase)
            if score > best_score:
                best, best_score = action, score
        if best and best_score >= 92:
            return Plan(actions=[(best[0], dict(best[1]))], source="fast")

        for rx, s, ex in self.templates:
            m = rx.match(t)
            if not m:
                continue
            if "n" in m.groupdict() and m.group("n") is not None:
                p = next((p for p in s.params if p.type == "integer"), None)
                if p is None:
                    continue
                v = int(m.group("n"))
                if (p.minimum is not None and v < p.minimum) or (p.maximum is not None and v > p.maximum):
                    continue
                return Plan(actions=[(s.name, {p.name: v})], source="fast")
            x = (m.groupdict().get("x") or "").strip()
            if "{site}" in ex and x in SITES:
                return Plan(actions=[(s.name, {"url": SITES[x]})], source="fast")
            if "{app}" in ex and self._is_known_app(x):
                return Plan(actions=[(s.name, {"name": x})], source="fast")
        return None

    def _is_known_app(self, x: str) -> bool:
        if x in BROWSER_WORDS or x in SYSTEM_APPS or x in ALIASES:
            return True
        if self.apps is None:
            return False
        hit = self.apps.resolve(x)
        return bool(hit) and fuzz.WRatio(x, hit[0]) >= 90
