"""Диктовка: «Рэм, записывай» → сказанное печатается туда, где курсор, пока не скажут «хватит».

GigaAM сам ставит знаки препинания и заглавные буквы, так что фраза печатается как есть.
Внутри диктовки понимаются только служебные фразы:
  «хватит», «стоп», «конец диктовки» — закончить (и в конце фразы: «…купить хлеба. Хватит.»);
  «новая строка», «с новой строки» — Shift+Enter: в мессенджерах это перенос, а не «отправить»;
  «сотри последнее», «удали последнее» — стереть последнюю напечатанную фразу.
30 секунд тишины — диктовка заканчивается сама.
"""
from __future__ import annotations

import re
from typing import Callable

IDLE_S = 30
STOP = {"хватит", "стоп", "конец диктовки", "закончи диктовку", "закончить диктовку", "хватит записывать",
        "останови диктовку", "стоп диктовка", "стоп запись", "конец записи", "хватит писать", "все хватит"}
NEWLINE = {"новая строка", "с новой строки", "новый абзац", "с новой строчки", "новая строчка", "абзац"}
UNDO = {"сотри последнее", "удали последнее", "сотри", "стереть", "сотри это", "удали это",
        "отмени последнее", "убери последнее", "стереть последнее"}


def _key(text: str) -> str:
    t = re.sub(r"[^а-яёa-z0-9 ]", " ", text.lower()).replace("ё", "е")
    return " ".join(t.split())


def split_stop(text: str) -> tuple[str, bool]:
    """«Купить хлеба. Хватит.» → («Купить хлеба.», True). Стоп-фраза — только отдельным
    предложением в конце, чтобы «хватит» внутри текста печаталось."""
    parts = [p for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p]
    if parts and _key(parts[-1]) in STOP:
        return " ".join(parts[:-1]), True
    return text.strip(), False


class Dictation:
    """Печатает фразы по одной. type_text/newline/backspace — действия клавиатуры
    (в проверках подменяются)."""

    def __init__(self, type_text: Callable[[str], None], newline: Callable[[], None],
                 backspace: Callable[[int], None]):
        self.type_text = type_text
        self.newline = newline
        self.backspace = backspace
        self.typed: list[str] = []          # напечатанные куски — чтобы стереть последний
        self.phrases = 0

    def feed(self, text: str) -> bool:
        """Очередная фраза. → False — диктовку закончили."""
        key = _key(text)
        if not key:
            return True
        if key in NEWLINE:
            self.newline()
            self.typed.append("\n")
            return True
        if key in UNDO:
            if self.typed:
                piece = self.typed.pop()
                self.backspace(len(piece))
            return True
        text, stop = split_stop(text)
        if text:
            piece = (" " if self.typed and self.typed[-1] != "\n" else "") + text
            self.type_text(piece)
            self.typed.append(piece)
            self.phrases += 1
        return not stop
