"""Действия с файлами голосом: последняя загрузка, последний скриншот, удалить в корзину,
переименовать, переложить в другую папку, очистить корзину.
Только быстрым путём (разбор фразы): модель этого умения не видит.

О каком файле речь: «последнюю загрузку», «последний скриншот» — понятно из фразы;
«этот файл», «это», «выделенное» — то, что выделено в окне проводника.
Удаление — только в корзину и только после «да»; очистка корзины — тоже после «да»."""
from __future__ import annotations

import os
import re
import shutil

from .. import winapi
from ..text import plural
from .base import Param, skill
from .system import FOLDER_RU, FOLDERS
from .files import EXECUTABLE

ACTIONS = ["open", "show", "delete", "rename", "move", "empty_bin"]
PARTIAL = {".crdownload", ".part", ".partial", ".tmp", ".opdownload", ".download"}
IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}

_DOWNLOAD = r"(?:последн\w+\s+(?:загрузк\w+|скачанн\w+(?:\s+файл\w*)?|скачк\w+)|что\s+(?:я\s+)?скачал\w*\s+последн\w+)"
_SHOT = r"(?:последн\w+\s+(?:скриншот\w*|снимок\s+экрана|скрин\w*))"
_THIS = r"(?:этот\s+файл|эти\s+файлы|этот|эту\s+папку|эту|это|его|ее|их|файл|файлы|выделенн\w+(?:\s+файл\w*)?)"
TARGET = rf"(?P<t>{_DOWNLOAD}|{_SHOT}|{_THIS})"
FOLDER_WORDS = {
    "загрузки": "downloads", "загрузках": "downloads", "закачки": "downloads",
    "документы": "documents", "документах": "documents", "мои документы": "documents",
    "рабочий стол": "desktop", "рабочем столе": "desktop", "на рабочий стол": "desktop",
    "изображения": "pictures", "картинки": "pictures", "фото": "pictures", "фотографии": "pictures",
    "музыку": "music", "музыка": "music", "видео": "videos",
}
_RULES = [
    ("open", re.compile(rf"^(?:открой|покажи|запусти|включи)\s+(?P<t>{_DOWNLOAD})$")),
    ("show", re.compile(rf"^(?:открой|покажи)\s+(?P<t>{_SHOT})$")),
    ("delete", re.compile(rf"^(?:удали|сотри|выкинь|выбрось)\s+{TARGET}(?:\s+в\s+корзину)?$")),
    ("delete", re.compile(rf"^(?:отправь|перемести|перенеси|кинь|скинь)\s+{TARGET}\s+в\s+корзину$")),
    ("rename", re.compile(rf"^переименуй\s+(?:{TARGET}\s+)?в\s+(?P<x>.+)$")),
    ("move", re.compile(rf"^(?:перемести|перенеси|переложи|скинь|отправь)\s+{TARGET}\s+(?:в|на)\s+(?P<x>.+)$")),
    ("empty_bin", re.compile(r"^(?:очисти|почисти|опустоши|вычисти)\s+корзину$")),
]


def _target_kind(t: str) -> str:
    if re.fullmatch(_DOWNLOAD, t):
        return "download"
    if re.fullmatch(_SHOT, t):
        return "screenshot"
    return "selected"


def _parse(text: str):
    from ..fastpath import clean                     # fastpath сам импортирует умения
    t = clean(text)
    for action, rx in _RULES:
        m = rx.match(t)
        if not m:
            continue
        g = m.groupdict()
        target = _target_kind(g["t"]) if g.get("t") else "selected"
        x = (g.get("x") or "").strip()
        if action == "rename":                        # имя — как сказано: с заглавными и «ё»
            m2 = re.search(r"переименуй\S*.*?\sв\s+(.+)$", text, re.I | re.S)
            x = m2.group(1).strip(" .!?,«»\"'") if m2 else x
        pronoun = bool(g.get("t")) and re.fullmatch(r"это|этот|эту|его|ее|их", g["t"]) is not None
        if action == "move":
            folder = FOLDER_WORDS.get(x.removeprefix("папку ").strip())
            if folder is None:
                return None                     # «перенеси окно на второй монитор» — это не сюда
            x = folder
        if action == "rename" and not x:
            return None
        if target == "selected" and pronoun:          # «удали это» — только в проводнике, с которым работают;
            w = winapi.foreground_window()            # иначе это про кнопку или текст — решает модель
            if not w or w["exe"].lower() != "explorer.exe":
                return None
            target = "here"
        return {"action": action, "target": target, "name": x}
    return None


def _newest(folders: list[str], exts: set[str] | None = None) -> str | None:
    best, best_t = None, 0.0
    for d in folders:
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            ext = os.path.splitext(e.name)[1].lower()
            if e.name.lower() == "desktop.ini" or ext in PARTIAL or (exts and ext not in exts):
                continue
            try:
                if not e.is_file():
                    continue
                t = e.stat().st_mtime
            except OSError:
                continue
            if t > best_t:
                best, best_t = e.path, t
    return best


def last_download() -> str | None:
    return _newest([winapi.known_folder("downloads")])


def last_screenshot() -> str | None:
    pics, vids = winapi.known_folder("pictures"), winapi.known_folder("videos")
    return _newest([os.path.join(pics, "Screenshots"), os.path.join(pics, "Снимки экрана"),
                    os.path.join(vids, "Captures"), pics], IMAGES)


def _files(ctx, target: str) -> list[str]:
    if target == "download":
        p = last_download()
        return [p] if p else []
    if target == "screenshot":
        p = last_screenshot()
        return [p] if p else []
    return winapi.explorer_selection(foreground_only=target == "here")


def _nothing(ctx, target: str) -> str:
    if target == "download":
        return "В загрузках пусто."
    if target == "screenshot":
        return "Скриншотов не нашлось."
    return ctx.phrase("Не вижу выделенных файлов. Выделите файл в проводнике и скажите ещё раз.",
                      "Рэм не видит выделенных файлов. Выделите файл в проводнике и скажите ещё раз.")


def _what(paths: list[str]) -> str:
    if len(paths) == 1:
        return f"«{os.path.basename(paths[0])}»"
    n = len(paths)
    return f"{n} {plural(n, 'файл', 'файла', 'файлов')}"


def _clean_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name).strip(" .")


@skill("file_actions", "Файлы: последняя загрузка, скриншот, удалить, переименовать",
       "Открыть последнюю загрузку или скриншот; удалить в корзину, переименовать или переложить "
       "выделенный файл; очистить корзину.",
       params=[Param("action", "string", "что сделать", enum=ACTIONS),
               Param("target", "string", "какой файл", enum=["download", "screenshot", "selected", "here"],
                     required=False),
               Param("name", "string", "новое имя или папка", required=False)],
       category="Файлы", model=False, parse=_parse)
def file_actions(ctx, action: str, target: str = "selected", name: str = ""):
    if action == "empty_bin":
        count, _ = winapi.recycle_bin()
        if not count:
            return "Корзина уже пуста."
        what = f"{count} {plural(count, 'объект', 'объекта', 'объектов')}"
        if not ctx.confirm(f"В корзине {what}. Очистить — без возврата"):
            return ctx.phrase("Хорошо, корзина не тронута.", "Хорошо, Рэм не будет.")
        winapi.empty_recycle_bin()
        return "Корзина очищена."

    paths = _files(ctx, target)
    if not paths:
        return _nothing(ctx, target)

    if action in ("open", "show"):
        p = paths[0]
        if os.path.splitext(p)[1].lower() in EXECUTABLE:
            winapi.open_path(os.path.dirname(p))     # программу так не запускаем — показываем папку
            return f"Последняя загрузка — программа «{os.path.basename(p)}». Открыта папка с ней."
        winapi.open_path(p)
        return None

    if action == "delete":
        if not ctx.confirm(f"Удалить {_what(paths)} в корзину"):
            return ctx.phrase("Хорошо, не удаляю.", "Хорошо, Рэм не будет.")
        if not winapi.recycle(paths):
            return "Удалить не получилось — файл открыт в другой программе?"
        return "Удалено в корзину."

    if action == "rename":
        if len(paths) != 1:
            return "Переименовать можно по одному файлу — выделите один."
        new = _clean_name(name)
        if not new:
            return "Не получилось разобрать новое имя."
        old = paths[0]
        if os.path.isfile(old) and not os.path.splitext(new)[1]:
            new += os.path.splitext(old)[1]          # расширение остаётся прежним
        dst = os.path.join(os.path.dirname(old), new)
        if os.path.exists(dst):
            return f"Файл «{new}» там уже есть."
        os.rename(old, dst)
        return f"Переименовано в «{new}»."

    # move
    folder = winapi.known_folder(name)
    moved = 0
    for p in paths:
        dst = os.path.join(folder, os.path.basename(p))
        if os.path.exists(dst) or os.path.dirname(os.path.abspath(p)) == os.path.abspath(folder):
            continue
        shutil.move(p, dst)
        moved += 1
    if not moved:
        return f"Переносить нечего: {_what(paths)} уже в папке «{FOLDER_RU.get(name, name)}»."
    return f"Перенесено в папку «{FOLDER_RU.get(name, name)}»: {_what(paths) if moved == len(paths) else moved}."


assert set(FOLDER_WORDS.values()) <= set(FOLDERS)
