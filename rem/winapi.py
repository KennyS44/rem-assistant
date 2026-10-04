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
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

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
    "win": 0x5B, "d": 0x44, "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
}
KEYEVENTF_KEYUP = 0x0002


def press(*keys: int) -> None:
    """Нажимает клавиши по порядку и отпускает в обратном (сочетание)."""
    _need_windows()
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(k, 0, KEYEVENTF_KEYUP, 0)


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
