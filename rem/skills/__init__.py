"""Все умения помощника. Новое умение — новый модуль здесь, импортированный ниже."""
from . import apps, media, shell, system, web  # noqa: F401 — регистрируют умения
from .base import REGISTRY, Param, Skill, skill  # noqa: F401
from .custom import build_custom


def active_skills(config: dict) -> list[Skill]:
    """Встроенные умения минус выключенные в настройках, плюс пользовательские.
    Для каждого умения можно переопределить «спрашивать подтверждение»."""
    disabled = set(config.get("disabled_skills", []))
    confirm = config.get("confirm_overrides", {})
    out = []
    for s in list(REGISTRY.values()) + build_custom(config.get("custom_skills", [])):
        if s.name in disabled:
            continue
        if s.name in confirm:
            s = Skill(**{**s.__dict__, "confirm": bool(confirm[s.name])})
        out.append(s)
    return out
