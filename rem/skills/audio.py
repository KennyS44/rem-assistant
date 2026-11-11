"""Звук: куда выводить (наушники / колонки / монитор) и громкость отдельной программы.
«Переключи звук на наушники», «звук на колонки», «переключи звук» — следующее устройство;
«сделай дискорд тише», «браузер громче», «громкость дискорда 30», «выключи звук в браузере».
Только быстрым путём (разбор фразы): модель этих умений не видит."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .. import winapi
from ..text import norm
from .apps import BROWSER_WORDS, _matching_pids
from .base import Param, skill
from .window import BROWSERS

KINDS = {   # что сказали → слова в названии устройства
    "headphones": ("науш", "headphone", "headset", "гарнитур", "airpods", "buds", "earphone", "hands-free"),
    "speakers": ("динамик", "колонк", "speaker", "акустик"),
    "monitor": ("hdmi", "displayport", "nvidia", "amd high definition", "intel(r) display", "монитор",
                "телевизор", " tv", "lg ", "samsung"),
}
SAID_KIND = {"наушник": "headphones", "гарнитур": "headphones", "блютуз": "headphones", "колонк": "speakers",
             "динамик": "speakers", "монитор": "monitor", "телевизор": "monitor", "телик": "monitor"}

GENERAL = {"звук", "громкость", "сделай", "еще", "чуть", "немного", "музыку", "музыка", "видео", "все", "его",
           "это", "чуть чуть", "еще немного"}
_OUT = re.compile(r"^(?:переключи|выведи|переведи|включи|поставь|сделай|верни|пусти)?\s*(?:звук|вывод звука|аудио)\s*"
                  r"(?:на|в|через)?\s*(?P<x>.*)$")
_APP_STEP = re.compile(r"^(?:сделай\s+)?(?:звук\s+(?:в|у)\s+)?(?P<x>.+?)\s+(?P<dir>тише|потише|громче|погромче)$")
_APP_STEP2 = re.compile(r"^(?:сделай\s+)?(?P<dir>тише|потише|громче|погромче)\s+(?:в|у)?\s*(?P<x>.+)$")
_APP_SET = re.compile(r"^(?:поставь\s+|сделай\s+)?громкость\s+(?:в|у)?\s*(?P<x>[а-яa-z][а-яa-z ]*?)\s+(?:на\s+)?(?P<n>\d{1,3})"
                      r"(?:\s*процент\w*|\s*%)?$")
_APP_MUTE = re.compile(r"^(?P<dir>выключи|включи|отключи|верни)\s+звук\s+(?:в|у)\s+(?P<x>.+)$")


def _parse_output(text: str):
    t = norm(text)
    m = _OUT.match(t)
    if not m or not t.split()[0] in ("переключи", "выведи", "переведи", "включи", "поставь", "сделай", "верни",
                                     "пусти", "звук", "вывод"):
        return None
    x = m.group("x").strip()
    if not x:
        return {"target": ""} if t.startswith(("переключи", "переведи")) else None
    if any(x.startswith(k) for k in SAID_KIND) or re.match(r"^(?:другое|следующее|другие)\b", x):
        return {"target": x}
    return None


def _parse_app(text: str):
    t = norm(text)
    if t.startswith("громкость") and not _APP_SET.match(t):
        return None
    for rx, kind in ((_APP_SET, "set"), (_APP_MUTE, "mute"), (_APP_STEP, "step"), (_APP_STEP2, "step")):
        m = rx.match(t)
        if not m:
            continue
        x = m.group("x").strip()
        if not x or x in GENERAL or x.split()[0] in ("на", "до") or len(x.split()) > 3:            # «сделай музыку тише» — общая громкость
            return None
        if kind == "set":
            return {"app": x, "level": int(m.group("n"))}
        if kind == "mute":
            return {"app": x, "level": 0 if m.group("dir") in ("выключи", "отключи") else -3}
        return {"app": x, "level": -1 if m.group("dir") in ("тише", "потише") else -2}
    return None


def pick_output(target: str, outputs: list[dict]) -> dict | None:
    """Устройство по сказанному: «наушники» — по типу, иначе по названию; пусто — следующее по кругу."""
    if not outputs:
        return None
    if not target or target.startswith(("другое", "следующее", "другие")):
        i = next((i for i, d in enumerate(outputs) if d["default"]), -1)
        return outputs[(i + 1) % len(outputs)]
    kind = next((k for said, k in SAID_KIND.items() if target.startswith(said)), None)
    if kind:
        hits = [d for d in outputs if any(w in d["name"].lower() for w in KINDS[kind])]
        others = [d for d in hits if not d["default"]]
        return (others or hits or [None])[0]
    scored = sorted(((fuzz.partial_ratio(norm(target), norm(d["name"])), d) for d in outputs), key=lambda x: -x[0])
    return scored[0][1] if scored[0][0] >= 75 else None


@skill("audio_output", "Звук: наушники / колонки",
       "Переключить вывод звука на наушники, колонки или монитор.",
       params=[Param("target", "string", "куда: наушники, колонки, монитор; пусто — следующее", required=False)],
       category="Медиа", model=False, parse=_parse_output)
def audio_output(ctx, target: str = ""):
    outs = winapi.audio_outputs()
    dev = pick_output(target, outs)
    if dev is None:
        names = "; ".join(d["name"] for d in outs) or "нет ни одного"
        return f"Такого устройства нет. Включены: {names}."
    if dev["default"]:
        return f"Звук и так идёт в «{dev['name']}»."
    winapi.set_audio_output(dev["id"])
    return f"Звук теперь в «{dev['name']}»."


def _app_pids(ctx, name: str) -> set[int]:
    import psutil
    q = norm(name)
    if q in BROWSER_WORDS or q in ("браузера", "браузере", "интернете"):
        return {p.pid for p in psutil.process_iter(["name"])
                if (p.info["name"] or "").lower().removesuffix(".exe") in BROWSERS}
    for cand in (q, q[:-1] if len(q) > 4 and q[-1] in "аеуы" else ""):     # «дискорда», «дискорде» → «дискорд»
        if cand:
            pids = _matching_pids(ctx, cand)
            if pids:
                return pids
    return set()


@skill("app_volume", "Громкость программы",
       "Громкость отдельной программы (как в микшере громкости): тише, громче, уровень, без звука.",
       params=[Param("app", "string", "программа"),
               Param("level", "integer", "0–100; −1 тише, −2 громче, −3 включить звук", minimum=-3, maximum=100)],
       category="Медиа", model=False, parse=_parse_app)
def app_volume(ctx, app: str, level: int):
    vols = winapi.app_audio(_app_pids(ctx, app))
    if not vols:
        return f"«{app}» сейчас не играет звук — громкость программы меняется, пока звук идёт."
    for v in vols:
        if level == -3:
            v.SetMute(0, None)
        elif level == 0:
            v.SetMute(1, None)
        elif level in (-1, -2):
            cur = v.GetMasterVolume()
            v.SetMasterVolume(max(0.0, min(1.0, cur + (-0.2 if level == -1 else 0.2))), None)
            v.SetMute(0, None)
        else:
            v.SetMasterVolume(max(0, min(100, level)) / 100, None)
            v.SetMute(0, None)
    return None
