"""Тонкая обёртка над Windows API через ctypes.

Вне Windows модуль импортируется без ошибок, а функции бросают NotOnWindows —
так логику помощника можно тестировать на любой системе.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

IS_WINDOWS = sys.platform == "win32"


class NotOnWindows(RuntimeError):
    pass


def _need_windows():
    if not IS_WINDOWS:
        raise NotOnWindows("действие доступно только в Windows")


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    powrprof = ctypes.WinDLL("powrprof")

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    for _fn in ("GetWindowTextLengthW", "IsWindowVisible", "IsIconic", "SetForegroundWindow"):
        getattr(user32, _fn).argtypes = [wintypes.HWND]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        @classmethod
        def from_str(cls, s):
            u = uuid.UUID(s)
            g = cls()
            g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
            g.Data4[:] = list(u.bytes[8:])
            return g


# ——— клавиши ———

VK = {
    "media_play_pause": 0xB3, "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "win": 0x5B, "d": 0x44, "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "enter": 0x0D,
}
KEYEVENTF_KEYUP = 0x0002


def press(*keys: int) -> None:
    """Нажимает клавиши по порядку и отпускает в обратном (сочетание)."""
    _need_windows()
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(k, 0, KEYEVENTF_KEYUP, 0)


def type_unicode(text: str, chunk: int = 32) -> None:
    """Напечатать текст в активное окно, как с клавиатуры, — любые буквы при любой раскладке.
    Переводы строк — пробелами: во многих программах Enter отправляет сообщение."""
    _need_windows()
    import time
    raw = " ".join(text.splitlines()).encode("utf-16-le")
    units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    for i in range(0, len(units), chunk):
        part = units[i:i + chunk]
        arr = (INPUT * (2 * len(part)))()
        for j, u in enumerate(part):
            for k, flags in enumerate((0x0004, 0x0004 | KEYEVENTF_KEYUP)):     # KEYEVENTF_UNICODE
                arr[2 * j + k].type = 1                                      # INPUT_KEYBOARD
                arr[2 * j + k].ki.wScan = u
                arr[2 * j + k].ki.dwFlags = flags
        user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT))
        time.sleep(0.02)


def is_foreground(hwnd: int) -> bool:
    return IS_WINDOWS and user32.GetForegroundWindow() == hwnd


# ——— папки ———

KNOWN_FOLDERS = {
    "desktop":   "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "pictures":  "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music":     "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos":    "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}


def known_folder(name: str) -> str:
    """Путь к системной папке с учётом переноса (например, в OneDrive)."""
    if not IS_WINDOWS:
        return os.path.join(os.path.expanduser("~"), name.capitalize())
    guid = GUID.from_str(KNOWN_FOLDERS[name])
    path = ctypes.c_wchar_p()
    shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path))
    try:
        return path.value
    finally:
        ole32.CoTaskMemFree(path)


def open_path(target: str) -> None:
    """Открывает файл, папку, ярлык или адрес программой по умолчанию."""
    _need_windows()
    os.startfile(target)  # noqa: в Windows есть


# ——— окна и процессы ———

WM_CLOSE = 0x0010


def windows_of_pids(pids: set[int]) -> list[int]:
    _need_windows()
    found = []

    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def open_windows() -> list[dict]:
    """Окна с панели задач: [{"hwnd", "title", "exe", "pid", "active"}], активное — первым.
    Невидимые, служебные и «спрятанные» окна приложений Магазина не попадают."""
    if not IS_WINDOWS:
        return []
    import psutil
    dwmapi = ctypes.WinDLL("dwmapi")
    fg, me, out = user32.GetForegroundWindow(), os.getpid(), []

    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if not n or not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):    # 4 — GW_OWNER
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x80:                 # GWL_EXSTYLE, WS_EX_TOOLWINDOW
            return True
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)   # DWMWA_CLOAKED
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if cloaked.value or pid.value == me:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == "Program Manager":
            return True
        try:
            exe = psutil.Process(pid.value).name()
        except psutil.Error:
            exe = ""
        out.append({"hwnd": hwnd, "title": buf.value, "exe": exe, "pid": pid.value, "active": hwnd == fg})
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return sorted(out, key=lambda w: not w["active"])


def focus_window(hwnd: int) -> None:
    """Вывести окно на передний план (свёрнутое — развернуть)."""
    _need_windows()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                                   # SW_RESTORE
    press(VK["alt"])            # иначе Windows не даёт фоновой программе переключать окна
    user32.SetForegroundWindow(hwnd)


def foreground_window() -> dict | None:
    """Окно, с которым сейчас работает человек: {"hwnd", "pid", "title", "exe"}.
    Окна самой Рэм не в счёт — тогда верхнее окно с панели задач. Диалоги тоже в счёт."""
    if not IS_WINDOWS:
        return None
    import psutil
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not hwnd or pid.value == os.getpid():
        wins = open_windows()
        return wins[0] if wins else None
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    try:
        exe = psutil.Process(pid.value).name()
    except psutil.Error:
        exe = ""
    return {"hwnd": hwnd, "pid": pid.value, "title": buf.value, "exe": exe}


def click_at(x: int, y: int) -> None:
    """Щелчок левой кнопкой мыши в точке экрана (в настоящих пикселях); курсор возвращается на место."""
    _need_windows()
    import time
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    prev = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    try:
        old = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(old))
        user32.SetCursorPos(x, y)
        user32.mouse_event(0x0002, 0, 0, 0, 0)                       # MOUSEEVENTF_LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)                       # MOUSEEVENTF_LEFTUP
        time.sleep(0.05)
        user32.SetCursorPos(old.x, old.y)
    finally:
        if prev:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))


def close_windows(hwnds: list[int]) -> None:
    """Вежливо закрывает окна — программа сама спросит про несохранённое."""
    _need_windows()
    for h in hwnds:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)


def foreground_is_fullscreen() -> bool:
    """Активное окно занимает весь монитор (игра, полноэкранное видео)."""
    if not IS_WINDOWS:
        return False
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):   # рабочий стол и панель задач
        return False
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    m = mi.rcMonitor
    return (rect.left <= m.left and rect.top <= m.top and
            rect.right >= m.right and rect.bottom >= m.bottom)


# ——— система ———

def lock_workstation() -> None:
    _need_windows()
    user32.LockWorkStation()


def sleep_pc() -> None:
    _need_windows()
    powrprof.SetSuspendState(False, True, False)


def shutdown(restart: bool = False) -> None:
    _need_windows()
    subprocess.Popen(["shutdown", "/r" if restart else "/s", "/t", "0"],
                     creationflags=0x08000000)  # CREATE_NO_WINDOW


def set_low_priority() -> None:
    """Приоритет «ниже обычного»: помощник уступает процессор всему остальному."""
    try:
        import psutil
        p = psutil.Process()
        if IS_WINDOWS:
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
    except Exception:
        pass


# ——— громкость (Core Audio через pycaw) ———

def _endpoint_volume():
    _need_windows()
    import comtypes
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    try:
        comtypes.CoInitialize()        # умения выполняются не в главном потоке
    except OSError:
        pass
    speakers = AudioUtilities.GetSpeakers()
    if hasattr(speakers, "EndpointVolume"):           # pycaw 2024+
        return speakers.EndpointVolume
    from comtypes import CLSCTX_ALL                   # старые версии pycaw
    iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))


def get_volume() -> int:
    return round(_endpoint_volume().GetMasterVolumeLevelScalar() * 100)


def set_volume(percent: int) -> None:
    ev = _endpoint_volume()
    ev.SetMute(0, None)
    ev.SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100, None)
