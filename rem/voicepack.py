"""Нейроголос Silero: отдельная загрузка и связь с программой RemVoice.

Silero работает на torch (~сотни МБ), поэтому он не входит в установщик: и Rem.exe,
и его обновления остаются маленькими. Голос скачивается один раз по кнопке в настройках
в %APPDATA%\\Rem\\voice:
    RemVoice\\RemVoice.exe  — программа озвучки (собирается в GitHub Actions, релиз voice-1)
    v5_ru.pt               — модель Silero с официального сайта (лицензия CC BY-NC-SA 4.0)
"""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

from .config import app_dir
from .models import _download

log = logging.getLogger("rem.voicepack")

PACK_URL = "https://github.com/KennyS44/rem-assistant/releases/download/voice-1/RemVoice.zip"
MODEL_URL = "https://models.silero.ai/models/tts/ru/v5_ru.pt"
SIZE_MB = 420                       # примерно: программа ~280 МБ + модель 145 МБ


def voice_dir() -> Path:
    d = app_dir() / "voice"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exe_path() -> Path:
    return voice_dir() / "RemVoice" / ("RemVoice.exe" if sys.platform == "win32" else "RemVoice")


def model_path() -> Path:
    return voice_dir() / "v5_ru.pt"


def ready() -> bool:
    return exe_path().exists() and model_path().exists()


def ensure(progress=None) -> None:
    """Скачать то, чего не хватает. progress(доля, подпись)."""
    if not exe_path().exists():
        z = voice_dir() / "RemVoice.zip"
        _download(PACK_URL, z, progress, "Программа озвучки")
        with zipfile.ZipFile(z) as f:
            f.extractall(voice_dir())
        z.unlink()
    if not model_path().exists():
        _download(MODEL_URL, model_path(), progress, "Модель голоса (145 МБ)")


class NeuralVoice:
    """Запущенный RemVoice: модель загружается один раз, дальше ~0,3 с на фразу."""

    def __init__(self, cmd: list[str] | None = None, start_timeout: float = 60):
        cmd = cmd or [str(exe_path()), "--model", str(model_path())]
        flags = 0x08000000 if sys.platform == "win32" else 0      # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, encoding="utf-8",
                                     creationflags=flags)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        first = self._next(start_timeout)
        if not first.get("ready"):
            self.close()
            raise RuntimeError(first.get("error") or "RemVoice не ответил")
        self.speakers = first.get("speakers", [])
        self.out = voice_dir() / "say.wav"
        log.info("нейроголос готов: %s", ", ".join(self.speakers))

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                self.lines.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        self.lines.put({"ok": False, "ready": False, "error": "RemVoice завершился"})

    def _next(self, timeout: float) -> dict:
        try:
            return self.lines.get(timeout=timeout)
        except queue.Empty:
            return {"ok": False, "ready": False, "error": f"RemVoice не ответил за {timeout:.0f} с"}

    def synth(self, text: str, speaker: str = "xenia", pitch: int = 0, rate: int = 100) -> Path:
        req = {"text": text, "speaker": speaker, "pitch": pitch, "rate": rate, "out": str(self.out)}
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        ans = self._next(30)
        if not ans.get("ok"):
            raise RuntimeError(ans.get("error", "ошибка озвучки"))
        return self.out

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()
