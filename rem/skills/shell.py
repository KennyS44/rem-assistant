"""Любая команда: модель сама пишет команду PowerShell, Рэм выполняет её только после «да».

Перед выполнением Рэм произносит, что собирается сделать (description), а сама команда
видна в уведомлении и журнале actions.log. Вывод команды возвращается модели — она
пересказывает результат одной фразой («На диске C свободно 120 гигабайт»).
"""
from __future__ import annotations

import subprocess

from .. import winapi
from .base import Param, skill

TIMEOUT = 60


@skill("run_command", "Любая команда (PowerShell)",
       "Выполнить команду PowerShell, когда другой функции для просьбы нет: узнать что-то о "
       "компьютере (место на диске, заряд, IP, версия Windows), поменять настройку, "
       "запустить что-то с параметрами. command — одна команда PowerShell для Windows 10; "
       "description — что она делает, коротко по-русски, без технических слов "
       "(«узнать свободное место на диске C»). Пользователь подтвердит голосом.",
       params=[Param("command", "string", "команда PowerShell"),
               Param("description", "string", "что делает команда, коротко по-русски")],
       confirm=True, category="Система", retell=True)
def run_command(ctx, command: str, description: str = ""):
    winapi._need_windows()
    script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; " \
             + command
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", script], capture_output=True, timeout=TIMEOUT,
                           creationflags=0x08000000)                 # CREATE_NO_WINDOW
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"команда не уложилась в {TIMEOUT} с")
    out = p.stdout.decode("utf-8", "replace").strip()
    err = p.stderr.decode("utf-8", "replace").strip()
    if p.returncode != 0 and not out:
        raise RuntimeError(err[:500] or f"код {p.returncode}")
    return out[:3000]
