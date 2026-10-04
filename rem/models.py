"""Модели речи: где лежат, как скачать при первом запуске.

Всё хранится в %APPDATA%\\Rem\\models и после скачивания работает без интернета.
"""
from __future__ import annotations

import shutil
import urllib.request
import zipfile
from pathlib import Path

from .config import app_dir

VOSK_NAME = "vosk-model-small-ru-0.22"
VOSK_URL = f"https://alphacephei.com/vosk/models/{VOSK_NAME}.zip"
GIGAAM_BASE = "https://huggingface.co/istupakov/gigaam-v3-onnx/resolve/main/"
GIGAAM_FILES = ["config.json", "v3_e2e_ctc_vocab.txt", "v3_e2e_ctc.int8.onnx"]


def models_dir() -> Path:
    d = app_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def vosk_dir() -> Path:
    return models_dir() / VOSK_NAME


def gigaam_dir() -> Path:
    return models_dir() / "gigaam-v3-e2e-ctc"


def speech_models_ready() -> bool:
    return (vosk_dir() / "am" / "final.mdl").exists() and all(
        (gigaam_dir() / f).exists() for f in GIGAAM_FILES)


def _download(url: str, dest: Path, progress=None, label: str = "") -> None:
    """Скачивание во временный файл с прогрессом; при обрыве недокачанное не остаётся."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "rem-assistant"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
            done += len(block)
            if progress and total:
                progress(done / total, label)
    tmp.replace(dest)


def ensure_speech_models(progress=None) -> None:
    """progress(доля 0..1, подпись) — для окна первого запуска."""
    if not (vosk_dir() / "am" / "final.mdl").exists():
        zpath = models_dir() / f"{VOSK_NAME}.zip"
        _download(VOSK_URL, zpath, progress, "Модель слова активации (45 МБ)")
        with zipfile.ZipFile(zpath) as z:
            z.extractall(models_dir())
        zpath.unlink()
    gdir = gigaam_dir()
    gdir.mkdir(parents=True, exist_ok=True)
    for f in GIGAAM_FILES:
        if not (gdir / f).exists():
            label = "Модель распознавания речи (225 МБ)" if f.endswith(".onnx") else "Модель распознавания речи"
            _download(GIGAAM_BASE + f, gdir / f, progress, label)


def remove_partial() -> None:
    for p in models_dir().rglob("*.part"):
        p.unlink(missing_ok=True)
    if (models_dir() / f"{VOSK_NAME}.zip").exists() and not vosk_dir().exists():
        shutil.rmtree(vosk_dir(), ignore_errors=True)
