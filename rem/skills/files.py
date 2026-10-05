"""Поиск и открытие файлов — первый шаг многошаговых задач: «найди вчерашний отчёт и открой его».

find_files только смотрит (результат получает модель и решает, что дальше), open_file открывает
найденное. Программы (.exe, .bat…) так не запускаются — для них есть open_app и подтверждение.
"""
from __future__ import annotations

import datetime as dt
import os
import time

from .. import winapi
from ..text import norm, translit
from .base import Param, skill
from .system import FOLDERS

EXECUTABLE = {".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".js", ".msi", ".scr", ".reg"}
LIMIT_S = 3.0                       # дольше не ищем: голосовой ответ не должен ждать


def _roots(folder: str | None) -> list[str]:
    names = [folder] if folder in FOLDERS else FOLDERS
    out = []
    for n in names:
        try:
            out.append(winapi.known_folder(n))
        except Exception:
            pass
    return [r for r in out if r and os.path.isdir(r)]


@skill("find_files", "Найти файл",
       "Найти файлы и папки по части имени в папках пользователя (рабочий стол, загрузки, "
       "документы, изображения, музыка, видео). Возвращает пути и даты изменения, новые первыми — "
       "по ним можно выбрать нужный и открыть через open_file.",
       params=[Param("query", "string", "часть имени файла, без расширения"),
               Param("folder", "string", "только если пользователь назвал папку; иначе не указывать — "
                     "искать везде", enum=FOLDERS, required=False)],
       category="Файлы", retell=True)
def find_files(ctx, query: str, folder: str = ""):
    q = norm(query)
    variants = {q, translit(q)} - {""}
    found, deadline = [], time.monotonic() + LIMIT_S
    for root in _roots(folder or None):
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d not in ("node_modules", "AppData")]
            for name in dirs + files:
                n = norm(name)
                if any(v in n for v in variants):
                    path = os.path.join(dirpath, name)
                    try:
                        found.append((os.path.getmtime(path), path))
                    except OSError:
                        pass
            if time.monotonic() > deadline:
                break
    if not found and folder:                       # модель могла угадать папку зря — ищем везде
        return find_files(ctx, query)
    if not found:
        return f"Ничего не найдено по запросу «{query}»."
    found.sort(reverse=True)
    lines = [f"{p} (изменён {dt.datetime.fromtimestamp(t):%d.%m.%Y %H:%M})" for t, p in found[:10]]
    more = f"\n…и ещё {len(found) - 10}" if len(found) > 10 else ""
    return "\n".join(lines) + more


@skill("open_file", "Открыть файл",
       "Открыть файл или папку по полному пути программой по умолчанию. Путь бери из ответа find_files.",
       params=[Param("path", "string", "полный путь к файлу или папке")],
       category="Файлы")
def open_file(ctx, path: str):
    path = path.strip().strip('"')
    if not os.path.exists(path):
        return "Такого файла нет."
    if os.path.splitext(path)[1].lower() in EXECUTABLE:
        return "Это программа — её Рэм так не запускает."
    winapi.open_path(path)
    return None
