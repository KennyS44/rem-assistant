"""Описание умения и общий реестр.

Умение — это действие, которое помощник умеет выполнять. У каждого есть:
  * описание для языковой модели (по нему она выбирает умение);
  * параметры (из них строится JSON-схема ответа модели);
  * примеры фраз для «быстрого пути» — без обращения к модели;
  * обработчик, который выполняет действие и при желании возвращает фразу для ответа.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Param:
    name: str
    type: str                      # "string" | "integer"
    description: str
    enum: Optional[list[str]] = None
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    required: bool = True

    def schema(self) -> dict:
        s: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum:
            s["enum"] = list(self.enum)
        if self.minimum is not None:
            s["minimum"] = self.minimum
        if self.maximum is not None:
            s["maximum"] = self.maximum
        return s


@dataclass
class Skill:
    name: str
    title: str                     # для окна настроек
    description: str               # для языковой модели
    handler: Callable[..., Optional[str]]
    params: list[Param] = field(default_factory=list)
    # быстрый путь: «фраза», «фраза с {n}» или («фраза», {готовые аргументы})
    examples: list = field(default_factory=list)
    confirm: bool = False          # спрашивать «да/нет» перед выполнением
    category: str = "Прочее"
    retell: bool = False           # обработчик возвращает данные — модель пересказывает их фразой

    def args_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {p.name: p.schema() for p in self.params},
            "required": [p.name for p in self.params if p.required],
            "additionalProperties": False,
        }

    def signature(self) -> str:
        """Короткая строка для системного промпта: имя(параметры) — описание."""
        ps = []
        for p in self.params:
            t = "|".join(p.enum) if p.enum else ("число" if p.type == "integer" else "текст")
            ps.append(f"{p.name}: {t}" + ("" if p.required else "?"))
        return f"{self.name}({', '.join(ps)}) — {self.description}"


REGISTRY: dict[str, Skill] = {}


def skill(name: str, title: str, description: str, *, params=(), examples=(),
          confirm: bool = False, category: str = "Прочее", retell: bool = False):
    """Декоратор: регистрирует функцию как умение."""
    def wrap(fn):
        REGISTRY[name] = Skill(name, title, description, fn, list(params), list(examples),
                               confirm, category, retell)
        return fn
    return wrap
