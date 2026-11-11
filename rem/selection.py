"""Выделенный текст: Рэм копирует выделение (Ctrl+C) и возвращает буфер обмена как был.

Ничего не выделено — берётся то, что уже лежит в буфере. Буфер возвращается, только если
там был текст (картинку из буфера вернуть не получится — выделение тогда остаётся в нём).
"""
from __future__ import annotations

import logging
import time

from . import winapi

log = logging.getLogger("rem.selection")


def _open():
    import win32clipboard
    for _ in range(10):                      # буфер бывает занят другой программой
        try:
            win32clipboard.OpenClipboard()
            return win32clipboard
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("буфер обмена занят другой программой")


def clipboard_text() -> str:
    cb = _open()
    try:
        if cb.IsClipboardFormatAvailable(13):        # CF_UNICODETEXT
            return cb.GetClipboardData(13) or ""
        return ""
    finally:
        cb.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    cb = _open()
    try:
        cb.EmptyClipboard()
        cb.SetClipboardData(13, text)
    finally:
        cb.CloseClipboard()


def grab(timeout: float = 0.8) -> tuple[str, str]:
    """→ (текст, откуда: «selection» | «clipboard» | «»)."""
    if not winapi.IS_WINDOWS:
        raise winapi.NotOnWindows()
    seq = winapi.user32.GetClipboardSequenceNumber()
    old = clipboard_text()
    winapi.press(winapi.VK["ctrl"], winapi.VK["c"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and winapi.user32.GetClipboardSequenceNumber() == seq:
        time.sleep(0.03)
    if winapi.user32.GetClipboardSequenceNumber() == seq:
        return (old.strip(), "clipboard" if old.strip() else "")
    time.sleep(0.05)                          # программа могла ещё дописывать форматы
    text = clipboard_text()
    if old:
        try:
            set_clipboard_text(old)
        except Exception as e:
            log.warning("буфер не вернулся: %s", e)
    return (text.strip(), "selection") if text.strip() else (old.strip(), "clipboard" if old.strip() else "")
