"""Запуск вместе с Windows: запись в HKCU\\...\\Run (права администратора не нужны)."""
import sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Rem"


def _command() -> str:
    if getattr(sys, "frozen", False):                 # собранный Rem.exe
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m rem'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            winreg.QueryValueEx(k, NAME)
        return True
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except OSError:
                pass
