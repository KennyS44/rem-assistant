"""Запуск и закрытие программ.

Программы ищутся по ярлыкам меню «Пуск» — там есть почти всё установленное.
Название, которое выбрала модель («Discord», «дискорд», «телега»), сопоставляется
с ярлыками через словарь синонимов, транслитерацию и нечёткое сравнение.
"""
from __future__ import annotations

import os
from pathlib import Path

from rapidfuzz import fuzz, process

from .. import winapi
from ..text import norm, translit
from .base import Param, skill

# Синонимы: как говорят → как называется ярлык или программа
ALIASES = {
    "хром": "google chrome", "гугл хром": "google chrome", "chrome": "google chrome",
    "яндекс": "yandex", "яндекс браузер": "yandex", "эдж": "microsoft edge", "edge": "microsoft edge",
    "файрфокс": "firefox", "мозилла": "firefox", "опера": "opera",
    "телеграм": "telegram", "телега": "telegram", "дискорд": "discord", "стим": "steam",
    "спотифай": "spotify", "ватсап": "whatsapp", "вотсап": "whatsapp", "зум": "zoom",
    "скайп": "skype", "обс": "obs studio", "вскод": "visual studio code", "vs code": "visual studio code",
    "ворд": "word", "эксель": "excel", "поверпоинт": "powerpoint", "аутлук": "outlook",
    "фотошоп": "photoshop", "эпик": "epic games launcher", "торрент": "qbittorrent",
}

# Системные программы без ярлыков в «Пуске»
SYSTEM_APPS = {
    "калькулятор": "calc.exe", "calculator": "calc.exe",
    "блокнот": "notepad.exe", "notepad": "notepad.exe",
    "paint": "mspaint.exe", "пейнт": "mspaint.exe", "паинт": "mspaint.exe",
    "проводник": "explorer.exe", "explorer": "explorer.exe",
    "диспетчер задач": "taskmgr.exe", "task manager": "taskmgr.exe",
    "панель управления": "control.exe", "control panel": "control.exe",
    "параметры": "ms-settings:", "настройки": "ms-settings:", "settings": "ms-settings:",
    "командная строка": "cmd.exe", "терминал": "wt.exe", "powershell": "powershell.exe",
    "ножницы": "snippingtool.exe",
}

# Процессы системных программ (для закрытия)
SYSTEM_PROCS = {"calc.exe": "calculatorapp.exe", "notepad.exe": "notepad.exe",
                "mspaint.exe": "mspaint.exe", "taskmgr.exe": "taskmgr.exe"}

BROWSER_WORDS = {"браузер", "browser", "интернет"}
SKIP = ("uninstall", "удал", "readme", "help", "справк", "website", "license", "лиценз")


def _start_menu_dirs() -> list[Path]:
    dirs = []
    for env in ("PROGRAMDATA", "APPDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


class AppIndex:
    """Имя ярлыка (в нижнем регистре) → путь к ярлыку."""

    def __init__(self, dirs: list[Path] | None = None):
        self.dirs = dirs if dirs is not None else _start_menu_dirs()
        self.apps: dict[str, str] = {}
        self.refresh()

    def refresh(self) -> None:
        self.apps.clear()
        for d in self.dirs:
            if not d.exists():
                continue
            for p in d.rglob("*"):
                if p.suffix.lower() not in (".lnk", ".url", ".appref-ms"):
                    continue
                name = norm(p.stem)
                if not any(s in name for s in SKIP):
                    self.apps.setdefault(name, str(p))

    def resolve(self, spoken: str) -> tuple[str, str] | None:
        """(имя, путь) для произнесённого названия или None."""
        q = norm(spoken)
        if not q:
            return None
        if q in SYSTEM_APPS:
            return q, SYSTEM_APPS[q]
        q = ALIASES.get(q, q)
        if q in self.apps:
            return q, self.apps[q]
        if not self.apps:
            return None
        # сначала как есть, потом латиницей: «дискорд» → «diskord» ≈ «discord»
        for cand in dict.fromkeys([q, translit(q)]):
            hit = process.extractOne(cand, self.apps.keys(), scorer=fuzz.WRatio, score_cutoff=82)
            if hit:
                return hit[0], self.apps[hit[0]]
        return None


def _default_browser() -> str | None:
    """Путь к браузеру по умолчанию из реестра."""
    if not winapi.IS_WINDOWS:
        return None
    import shlex
    import winreg
    try:
        key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            prog_id = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        return shlex.split(cmd, posix=False)[0].strip('"')
    except OSError:
        return None


@skill("open_app", "Открыть программу",
       "Запустить программу или игру по названию. name — название как в меню «Пуск», "
       "обычно латиницей: Google Chrome, Telegram, Steam, Discord; для системных — по-русски: "
       "калькулятор, блокнот, проводник, диспетчер задач, параметры. "
       "Для просьбы открыть просто браузер name = браузер.",
       params=[Param("name", "string", "название программы")],
       examples=["открой {app}", "запусти {app}", "включи {app}"],
       category="Программы")
def open_app(ctx, name: str):
    if norm(name) in BROWSER_WORDS:
        browser = _default_browser()
        winapi.open_path(browser or "https://www.google.com")
        return None
    hit = ctx.apps.resolve(name)
    if not hit:
        return f"Программа «{name}» не найдена."
    winapi.open_path(hit[1])
    return None


def _matching_pids(ctx, name: str) -> set[int]:
    import psutil
    q = norm(name)
    targets = set()
    if q in SYSTEM_APPS:
        exe = SYSTEM_APPS[q]
        targets.add(SYSTEM_PROCS.get(exe, exe).removesuffix(".exe"))
    hit = None if q in SYSTEM_APPS else ctx.apps.resolve(name)
    for word in {ALIASES.get(q, q), translit(q), hit[0] if hit else ""}:
        if word:
            targets.add(word.split()[-1] if " " in word else word)   # «google chrome» → «chrome»
    pids = set()
    for p in psutil.process_iter(["pid", "name"]):
        pname = (p.info["name"] or "").lower().removesuffix(".exe")
        if any(t and (t == pname or fuzz.ratio(t, pname) >= 85) for t in targets):
            pids.add(p.info["pid"])
    return pids


@skill("close_app", "Закрыть программу",
       "Закрыть запущенную программу по названию (окна закрываются вежливо, "
       "несохранённое программа спросит сама).",
       params=[Param("name", "string", "название программы")],
       examples=["закрой {app}", "выключи {app}"],
       category="Программы")
def close_app(ctx, name: str):
    pids = _matching_pids(ctx, name)
    hwnds = winapi.windows_of_pids(pids) if pids else []
    if not hwnds:                                  # «закрой окно с отчётом» — по заголовку
        hwnds = [w["hwnd"] for w in find_windows(ctx, name)[:1]]
    if not hwnds:
        return f"Программа «{name}» не запущена."
    winapi.close_windows(hwnds)
    return None


def find_windows(ctx, name: str) -> list[dict]:
    """Открытые окна программы name или с name в заголовке; лучшие — первыми."""
    wins = winapi.open_windows()
    pids = _matching_pids(ctx, name)
    hits = [w for w in wins if w["pid"] in pids]
    if hits:
        return hits
    q = norm(name)
    scored = [(fuzz.partial_ratio(q, norm(w["title"] + " " + w["exe"].removesuffix(".exe"))), w) for w in wins]
    return [w for score, w in sorted(scored, key=lambda x: -x[0]) if score >= 80]


@skill("switch_window", "Переключиться на окно",
       "Показать уже открытое окно: вывести на передний план, свёрнутое — развернуть. "
       "name — название программы или слова из заголовка окна (см. список открытых окон).",
       params=[Param("name", "string", "программа или часть заголовка окна")],
       examples=["переключись на {app}", "перейди в {app}", "разверни {app}"],
       category="Программы")
def switch_window(ctx, name: str):
    hits = find_windows(ctx, name)
    if not hits:
        return f"Окно «{name}» не найдено."
    winapi.focus_window(hits[0]["hwnd"])
    return None


@skill("list_windows", "Что открыто", "Перечислить открытые окна (на вопрос «что открыто?»).",
       examples=["что открыто", "что у меня открыто", "какие окна открыты", "какие программы открыты"],
       category="Программы")
def list_windows(ctx):
    titles = [w["title"][:60] for w in winapi.open_windows()]
    if not titles:
        return "Открытых окон нет."
    more = f" и ещё {len(titles) - 8}" if len(titles) > 8 else ""
    return "Открыто: " + "; ".join(titles[:8]) + more + "."
