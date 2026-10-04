"""Точка входа для PyInstaller (у rem/__main__.py относительные импорты).

У собранного Rem.exe нет консоли: необработанная ошибка показала бы окно и
повисла бы без ответа. Поэтому любую ошибку пишем в %APPDATA%\\Rem\\crash.txt.
"""
import os
import sys
import traceback


def _crash_file():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "Rem")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "crash.txt")


try:
    from rem.__main__ import main
    code = main()
except SystemExit:
    raise
except BaseException:
    with open(_crash_file(), "w", encoding="utf-8") as f:
        traceback.print_exc(file=f)
    code = 3
sys.exit(code)
