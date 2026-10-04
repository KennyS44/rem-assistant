"""Готовые фразы голосом Рем: самые частые ответы, озвученные заранее.

Голос создан по текстовому описанию моделью Qwen3-TTS VoiceDesign (Apache 2.0) — это
новый синтетический голос, ничьи записи не использовались. Озвучка тяжёлая (модель 1,7 млрд
параметров), поэтому фразы готовятся один раз при сборке (voice/design.py) и лежат в rem/clips.
Остальное Рэм говорит нейроголосом Silero, подстроенным под этот голос.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

CLIPS = Path(__file__).parent / "clips"
TIMER_MINUTES = (1, 2, 3, 5, 10, 15, 20, 25, 30, 40, 45, 60, 90, 120)


def phrases() -> list[str]:
    """Всё, что озвучивается заранее. Меняешь список — пересобери клипы (voice/design.py pack)."""
    from .skills import REGISTRY
    from .text import duration_ru
    out = ["Рэм слушает.", "Рэм всё сделала.", "Простите, Рэм не расслышала.", "Хорошо, Рэм не будет.",
           "Таймеры отменены.", "Таймеров нет.", "На сколько поставить таймер?"]
    for m in TIMER_MINUTES:
        label = duration_ru(m * 60)
        out += [f"Таймер на {label}.", f"Таймер на {label} закончился."]
    for s in sorted(REGISTRY.values(), key=lambda s: s.name):
        if s.confirm:
            out.append(f"{s.title}? Скажите «да» или «нет».")
    return out


def key(text: str) -> str:
    norm = " ".join(text.lower().replace("ё", "е").split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def clip(text: str) -> Path | None:
    p = CLIPS / f"{key(text)}.wav"
    return p if p.exists() else None


def clips_for(text: str) -> list[Path] | None:
    """Клипы для каждого предложения ответа — или None, если хоть одного нет
    (тогда весь ответ говорит нейроголос: смена голоса посреди фразы режет слух)."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
    found = [clip(p) for p in parts]
    return found if found and all(found) else None
