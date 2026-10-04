"""Обновление без ручной переустановки.

Рэм сам смотрит последний релиз на GitHub, скачивает RemSetup.exe в фоне и спрашивает,
поставить ли его. Установщик запускается тихо поверх текущей версии: настройки,
модели речи, нейроголос и Ollama остаются на месте, автозапуск не трогается.
После установки Рэм запускается снова (параметр /UPDATE=1, см. installer.iss).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import app_dir
from .models import _download

log = logging.getLogger("rem.update")

API = "https://api.github.com/repos/KennyS44/rem-assistant/releases/latest"
ASSET = "RemSetup.exe"


@dataclass
class Release:
    version: str
    url: str
    size: int
    notes: str = ""


def parse_version(v: str) -> tuple[int, ...]:
    """«v0.2.0» → (0, 2, 0). Не версия — пустой кортеж (меньше любой)."""
    m = re.match(r"^v?(\d+(?:\.\d+)*)$", v.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def newer(remote: str, local: str | None = None) -> bool:
    return parse_version(remote) > parse_version(local or __version__)


def from_api(data: dict) -> Release | None:
    """Ответ GitHub → релиз с установщиком (или None, если установщика нет)."""
    for a in data.get("assets", []):
        if a.get("name") == ASSET:
            return Release(data.get("tag_name", "").lstrip("v"), a["browser_download_url"],
                           int(a.get("size", 0)), data.get("body") or "")
    return None


def latest() -> Release | None:
    req = urllib.request.Request(API, headers={"User-Agent": "rem-assistant",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return from_api(json.load(r))


def can_update() -> bool:
    """Только установленный Rem.exe: в режиме разработки ставить нечего и некуда."""
    return bool(getattr(sys, "frozen", False)) and (Path(sys.executable).parent / "unins000.exe").exists()


def update_dir() -> Path:
    d = app_dir() / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup() -> None:
    """Удалить скачанные установщики — после обновления они не нужны."""
    for p in update_dir().glob("RemSetup-*"):
        m = re.match(r"RemSetup-(.+)\.exe(\.part)?$", p.name)
        if not m or not newer(m.group(1)) or m.group(2):
            p.unlink(missing_ok=True)


def download(rel: Release, progress=None) -> Path:
    path = update_dir() / f"RemSetup-{rel.version}.exe"
    if path.exists() and (not rel.size or path.stat().st_size == rel.size):
        return path
    _download(rel.url, path, progress, f"Рэм {rel.version}")
    if rel.size and path.stat().st_size != rel.size:
        path.unlink(missing_ok=True)
        raise RuntimeError("установщик скачался не полностью")
    return path


def install(path: Path) -> None:
    """Запустить тихую установку поверх. Рэм после этого должен сразу выйти —
    установщик заменит файлы и запустит новую версию."""
    args = [str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/FORCECLOSEAPPLICATIONS",
            "/MERGETASKS=!autostart,!desktopicon,!ollama", "/UPDATE=1"]
    log.info("запускаю обновление: %s", " ".join(args))
    subprocess.Popen(args, close_fds=True)
