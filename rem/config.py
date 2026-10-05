"""Настройки: %APPDATA%\\Rem\\config.json. Неизвестные ключи сохраняются как есть."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULTS: dict = {
    "wake_word": "рэм",
    "model": "qwen3:4b-instruct-2507-q4_K_M",
    "cloud_model": "",            # облачная модель Ollama («…:cloud»); пусто — только модель на компьютере
    "ollama_url": "http://127.0.0.1:11434",
    "brain_mode": "tools",        # tools — родной вызов функций; schema — ответ по JSON-схеме
    "keep_alive_min": 3,          # сколько минут держать модель в видеопамяти после команды
    "game_mode": "fast_only",     # при полноэкранной игре: fast_only | cpu | off
    "asr_threads": 3,             # ядра на распознавание речи (из 6)
    "search_engine": "google",    # google | yandex
    "voice_engine": "windows",    # windows — голос Windows; silero — нейроголос (скачивается отдельно)
    "voice": "",                  # голос Windows; пусто — первый русский
    "silero_speaker": "xenia",    # xenia | baya | kseniya | aidar | eugene
    "voice_pitch": 0,             # высота, % от -30 до +30
    "voice_rate": 100,            # темп, % от 60 до 140
    "voice_timbre": 0,            # тембр моложе, % от 0 до 15 (только нейроголос)
    "voice_clips": False,         # частые фразы — готовыми записями голосом Рем (rem/clips)
    "rem_style": False,           # «Рэм слушает» и «Сделано» голосом вместо сигналов
    "speak_replies": True,
    "mic_device": None,           # имя микрофона; None — вход Windows по умолчанию
    "ignore_speakers": True,      # не реагировать на «Рэм» из колонок (фильмы, видео)
    "auto_update": True,          # проверять и скачивать обновления
    "disabled_skills": [],
    "confirm_overrides": {},
    "custom_skills": [],
}


def app_dir() -> Path:
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    d = Path(base) / "Rem" if base else Path.home() / ".rem"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return app_dir() / "config.json"


def load(path: Path | None = None) -> dict:
    path = path or config_path()
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError):
        # битый файл не должен ломать запуск: сохраняем копию и стартуем с настроек по умолчанию
        path.replace(path.with_suffix(".broken.json"))
    return cfg


def save(cfg: dict, path: Path | None = None) -> None:
    path = path or config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)               # атомарно: при сбое старый файл останется целым
