"""Выученные команды: что модель один раз поняла, в следующий раз выполняется сразу, без неё.

«Включи что-нибудь спокойное» → модель решила: open_site(url=…). Запоминаем фразу и действия
в %APPDATA%\\Rem\\phrasebook.json — в следующий раз ответ мгновенный и видеокарта не нужна
(в игре тоже работает).

Запоминаем осторожно — только то, что не зависит от обстановки:
  * одно обращение к модели, всё выполнено без ошибок;
  * без подтверждения «да/нет», без команд PowerShell, без кнопок и полей в окнах (там важно,
    что сейчас на экране), без пересказа данных (ответ каждый раз свой);
  * во фразе нет слов, отсылающих к прошлому разговору: «закрой его», «ещё громче», «то же».
«Рэм, не то» сразу после выполнения — забыть фразу; «забудь выученные команды» — забыть все.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path

from .fastpath import clean

log = logging.getLogger("rem.phrasebook")

LIMIT = 500                         # больше — забываем то, чем дольше всего не пользовались
NO_LEARN = {"run_command", "type_text", "click_button", "look_at_window", "switch_tab", "open_chat",
            "dictation"}
# слова, при которых смысл зависит от сказанного раньше или от того, что на экране
CONTEXT_WORDS = {
    "его", "ее", "их", "им", "ему", "ей", "него", "нее", "них", "он", "она", "оно", "они",
    "это", "этот", "эта", "эту", "этого", "этой", "эти", "этих", "том", "тот", "та", "ту", "те", "то",
    "там", "туда", "тут", "здесь", "сюда", "оттуда", "обратно", "назад",
    "еще", "тоже", "также", "снова", "опять", "заново", "так", "такой", "такую", "такое",
    "предыдущий", "предыдущую", "прошлый", "прошлую", "последний", "последнюю",
    "следующий", "следующую", "дальше", "лучше", "сильнее", "больше", "меньше",
}
UNDO = {"не то", "не так", "неправильно", "не правильно", "это не то", "ты ошиблась", "ошиблась",
        "не то сделала", "ты не так поняла", "не так поняла"}
FORGET_ALL = {"забудь выученные команды", "забудь все выученные команды", "забудь выученное",
              "забудь все команды которые выучила", "забудь что выучила"}


def learnable(text: str, actions: list[tuple[str, dict]], skills: dict) -> bool:
    key = clean(text)
    if not key or not actions or len(key.split()) > 12:
        return False
    if set(key.split()) & CONTEXT_WORDS:
        return False
    for name, _ in actions:
        s = skills.get(name)
        if s is None or name in NO_LEARN or s.confirm or s.retell or s.observe is not None:
            return False
    return True


class Phrasebook:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.items = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("actions")}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("выученные команды не прочитались: %s", e)

    def __len__(self) -> int:
        return len(self.items)

    def get(self, text: str) -> list[tuple[str, dict]] | None:
        with self.lock:
            item = self.items.get(clean(text))
            if not item:
                return None
            item["used"] = dt.datetime.now().isoformat(timespec="seconds")
            return [(n, dict(a)) for n, a in item["actions"]]

    def learn(self, text: str, actions: list[tuple[str, dict]]) -> None:
        key = clean(text)
        with self.lock:
            now = dt.datetime.now().isoformat(timespec="seconds")
            self.items[key] = {"actions": [[n, a] for n, a in actions], "said": text, "used": now}
            if len(self.items) > LIMIT:
                for k in sorted(self.items, key=lambda k: self.items[k].get("used", ""))[:len(self.items) - LIMIT]:
                    del self.items[k]
        log.info("выучила: «%s» → %s", key, actions)
        self.save()

    def forget(self, text: str) -> bool:
        with self.lock:
            gone = self.items.pop(clean(text), None) is not None
        if gone:
            log.info("забыла: «%s»", clean(text))
            self.save()
        return gone

    def clear(self) -> int:
        with self.lock:
            n = len(self.items)
            self.items = {}
        self.save()
        return n

    def save(self) -> None:
        with self.lock:
            data = json.dumps(self.items, ensure_ascii=False, indent=1)
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("выученные команды не сохранились: %s", e)
