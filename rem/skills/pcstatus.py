"""Состояние компьютера: заряд, место на дисках, что грузит процессор и память, закрыть то, что тормозит.
Только быстрым путём (разбор фразы): модель этих умений не видит."""
from __future__ import annotations

import re
import time

from .. import winapi
from ..text import duration_ru, plural
from .base import Param, skill
from .info import _key, num

_BATTERY = re.compile(r"\b(заряд\w*|батаре\w*|аккумулятор\w*)\b")
_DISK = re.compile(r"\b(мест\w*)\b.*\b(диск\w*|компьютер\w*|осталось)\b|\bсвободн\w* мест|\bсколько места\b")
_LOAD = re.compile(r"\b(что|кто|почему)\s+(\w+\s+)?(грузит|тормозит|нагружает|жрет|ест|тормозят|лагает)\b|"
                   r"\bзагрузк\w* (процессора|компьютера|памяти|цп)\b|\bсколько (памяти|оперативки|оперативной)\b|"
                   r"\bпочему (комп\w*|все|компьютер) (тормоз\w*|лага\w*|виснет)")
_KILL = re.compile(r"^(?:закрой|убей|выключи|заверши|останови)\s+(?:то\s+|ту\s+программу\s+)?(?:что|кто|которая)\s+"
                   r"(?:тормозит|грузит|жрет|нагружает|тормозит компьютер|грузит компьютер)\b")
SKIP = {"system idle process", "system", "idle", "memory compression", "registry", "secure system", "rem"}


def _parse(text: str):
    t = _key(text)
    if _KILL.match(t):
        return {"what": "close_heavy"}
    if _LOAD.search(t):
        return {"what": "load"}
    if _DISK.search(t):
        return {"what": "disk"}
    if _BATTERY.search(t) and not t.startswith(("открой", "включи", "выключи")) and "таймер" not in t:
        return {"what": "battery"}
    return None


def _pct(n: float) -> str:
    n = round(n)
    return f"{n} {plural(n, 'процент', 'процента', 'процентов')}"


def _gb(b: float) -> str:
    g = b / 1024 ** 3
    g = round(g, 1) if g < 10 else round(g)
    word = "гигабайта" if g != int(g) else plural(int(g), "гигабайт", "гигабайта", "гигабайт")   # «0,3 гигабайта»
    return f"{num(g)} {word}"


def battery_text() -> str:
    import psutil
    b = psutil.sensors_battery()
    if b is None:
        return "У этого компьютера нет батареи — он работает от сети."
    out = f"Заряд {_pct(b.percent)}"
    if b.power_plugged:
        out += ", заряжается" if b.percent < 100 else ", от сети"
    elif b.secsleft and b.secsleft > 0 and b.secsleft != psutil.POWER_TIME_UNLIMITED:
        out += f", хватит примерно на {duration_ru(int(b.secsleft) // 60 * 60)}"
    return out + "."


def disk_text() -> str:
    import psutil
    parts, seen = [], set()
    for p in psutil.disk_partitions(all=False):
        if "cdrom" in p.opts or not p.fstype or p.device in seen:
            continue
        seen.add(p.device)
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        letter = p.device.rstrip(":\\/")[-1:].upper() or p.mountpoint
        parts.append(f"на диске {letter} свободно {_gb(u.free)} из {_gb(u.total)}")
    if not parts:
        return "Не получилось узнать место на дисках."
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."


def heavy(sample_s: float = 1.0) -> list[dict]:
    """Программы по нагрузке: [{"name", "cpu" (% всего процессора), "mem" (байт), "pids"}], тяжёлые — первыми.
    Процессы одной программы (вкладки Chrome) складываются."""
    import os

    import psutil
    psutil.cpu_percent(None)                      # отсчёт общей загрузки — с этого момента
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            p.cpu_percent(None)
            procs.append(p)
        except psutil.Error:
            pass
    time.sleep(sample_s)
    n = psutil.cpu_count() or 1
    agg: dict[str, dict] = {}
    me = os.getpid()
    for p in procs:
        try:
            name = (p.info["name"] or "").removesuffix(".exe")
            if not name or name.lower() in SKIP or p.pid in (0, me):
                continue
            a = agg.setdefault(name.lower(), {"name": name, "cpu": 0.0, "mem": 0, "pids": set()})
            a["cpu"] += p.cpu_percent(None) / n
            a["mem"] += p.info["memory_info"].rss if p.info["memory_info"] else 0
            a["pids"].add(p.pid)
        except psutil.Error:
            pass
    return sorted(agg.values(), key=lambda a: (-a["cpu"], -a["mem"]))


def load_text() -> str:
    import psutil
    apps = heavy()
    cpu = psutil.cpu_percent(None)
    mem = psutil.virtual_memory()
    head = f"Процессор занят на {_pct(cpu)}, память — на {_pct(mem.percent)}. "
    busy = [a for a in apps[:3] if a["cpu"] >= 2]
    if busy:
        parts = [f"{a['name']} — {_pct(a['cpu'])} процессора, {_gb(a['mem'])} памяти" for a in busy]
        return head + "Больше всего грузит: " + "; ".join(parts) + "."
    parts = [f"{a['name']} — {_gb(a['mem'])}" for a in sorted(apps, key=lambda a: -a["mem"])[:3]]
    return head + "Процессор почти свободен. Больше всего памяти у: " + "; ".join(parts) + "."


@skill("pc_status", "Состояние компьютера",
       "Заряд батареи, свободное место на дисках, что грузит процессор и память; закрыть то, что тормозит.",
       params=[Param("what", "string", "что узнать", enum=["battery", "disk", "load", "close_heavy"])],
       category="Система", model=False, parse=_parse)
def pc_status(ctx, what: str):
    if what == "battery":
        return battery_text()
    if what == "disk":
        return disk_text()
    if what == "load":
        return load_text()
    # закрыть то, что тормозит: самая тяжёлая программа с окном — только после «да»
    with_windows = {w["pid"] for w in winapi.open_windows()}
    apps = [a for a in heavy() if a["pids"] & with_windows]
    if not apps:
        return "Тяжёлых программ с окнами не видно."
    a = apps[0] if apps[0]["cpu"] >= 5 else max(apps, key=lambda a: a["mem"])
    why = (f"грузит процессор на {_pct(a['cpu'])}" if a["cpu"] >= 5 else f"занимает {_gb(a['mem'])} памяти")
    if not ctx.confirm(f"Сильнее всего тормозит {a['name']}: {why}. Закрыть"):
        return ctx.phrase("Хорошо, не закрываю.", "Хорошо, Рэм не будет.")
    winapi.close_windows([w["hwnd"] for w in winapi.open_windows() if w["pid"] in a["pids"]])
    return None
