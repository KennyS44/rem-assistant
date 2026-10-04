"""Умения, которые пользователь добавляет сам в окне настроек — без кода.

Каждое описывается в config.json так:
    {"title": "Открыть Discord", "type": "open", "target": "C:\\...\\Discord.lnk",
     "phrases": "дискорд, дис", "confirm": false}

Типы:
    open — открыть программу, файл, папку или сайт (target — путь или адрес);
    keys — нажать сочетание клавиш (target — например «win+shift+s»).
"""
from __future__ import annotations

from .. import winapi
from .base import Skill

KEY_NAMES = {
    "win": 0x5B, "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "tab": 0x09, "enter": 0x0D,
    "esc": 0x1B, "space": 0x20, "printscreen": 0x2C, "delete": 0x2E, "home": 0x24, "end": 0x23,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


def parse_keys(combo: str) -> list[int]:
    """«win+shift+s» → коды клавиш. Неизвестная клавиша — ValueError."""
    codes = []
    for part in combo.lower().replace(" ", "").split("+"):
        if part in KEY_NAMES:
            codes.append(KEY_NAMES[part])
        elif len(part) == 1 and part.isalnum():
            codes.append(ord(part.upper()))
        else:
            raise ValueError(f"неизвестная клавиша «{part}»")
    return codes


def build_custom(items: list[dict]) -> list[Skill]:
    skills = []
    for i, item in enumerate(items or []):
        title = (item.get("title") or "").strip()
        target = (item.get("target") or "").strip()
        kind = item.get("type", "open")
        if not title or not target:
            continue
        if kind == "keys":
            codes = parse_keys(target)

            def handler(ctx, _codes=codes):
                winapi.press(*_codes)
        else:
            def handler(ctx, _t=target):
                winapi.open_path(_t)

        phrases = [p.strip() for p in (item.get("phrases") or "").split(",") if p.strip()]
        desc = f"Пользовательское действие: {title}."
        if phrases:
            desc += " Так говорят: " + "; ".join(phrases) + "."
        skills.append(Skill(
            name=f"custom_{i + 1}", title=title, description=desc, handler=handler,
            examples=[title.lower()] + [p.lower() for p in phrases],
            confirm=bool(item.get("confirm")), category="Мои умения",
        ))
    return skills
