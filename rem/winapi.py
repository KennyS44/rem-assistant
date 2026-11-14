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
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]

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
    "tab": 0x09, "a": 0x41, "c": 0x43, "backspace": 0x08, "left": 0x25, "right": 0x27,
    "f": 0x46, "s": 0x53, "t": 0x54, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59, "z": 0x5A,
    "f4": 0x73, "f5": 0x74, "home": 0x24, "end": 0x23, "esc": 0x1B, "space": 0x20,
    "pgup": 0x21, "pgdn": 0x22,
}
KEYEVENTF_KEYUP = 0x0002


def press(*keys: int) -> None:
    """Нажимает клавиши по порядку и отпускает в обратном (сочетание)."""
    _need_windows()
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(k, 0, KEYEVENTF_KEYUP, 0)


def scroll(notches: int) -> None:
    """Колесо мыши: > 0 — вверх, < 0 — вниз. Листается то окно, над которым мышь (как рукой)."""
    _need_windows()
    user32.mouse_event(0x0800, 0, 0, ctypes.c_int(notches * 120), 0)     # MOUSEEVENTF_WHEEL; вниз — отрицательное


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
    if user32.GetForegroundWindow() == hwnd:
        return                  # уже впереди; лишний Alt включил бы в окне режим меню
    press(VK["alt"])            # иначе Windows не даёт фоновой программе переключать окна
    user32.SetForegroundWindow(hwnd)


SHOW = {"minimize": 6, "maximize": 3, "restore": 9}     # SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE


def window_state(hwnd: int) -> str:
    """«minimize» — свёрнуто, «maximize» — на весь экран, «restore» — обычное."""
    _need_windows()
    return "minimize" if user32.IsIconic(hwnd) else "maximize" if user32.IsZoomed(hwnd) else "restore"


def show_window(hwnd: int, how: str) -> None:
    """Свернуть / развернуть на весь экран / вернуть обычный размер."""
    _need_windows()
    user32.ShowWindow(hwnd, SHOW[how])
    if how != "minimize":
        focus_window(hwnd)


def window_half(hwnd: int, side: str) -> tuple[int, int, int, int]:
    """Поставить окно на левую или правую половину его монитора (без панели задач).
    Не через Win+стрелку: та ещё предлагает выбрать окно на другую половину. → (x, y, w, h) половины."""
    _need_windows()
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    prev = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2 — честные пиксели
    try:
        if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
            user32.ShowWindow(hwnd, 9)                                   # SW_RESTORE
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))
        work = mi.rcWork
        w, h = (work.right - work.left) // 2, work.bottom - work.top
        x = work.left if side == "left" else work.left + w
        # невидимая рамка (тень) окна Windows 10/11: без поправки между окном и краем экрана щель
        outer, frame = wintypes.RECT(), wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(outer))
        ml = mt = mr = mb = 0
        if not ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame)):
            ml, mt = frame.left - outer.left, frame.top - outer.top           # DWMWA_EXTENDED_FRAME_BOUNDS
            mr, mb = outer.right - frame.right, outer.bottom - frame.bottom
        user32.SetWindowPos(hwnd, None, x - ml, work.top - mt, w + ml + mr, h + mt + mb, 0x0004)   # SWP_NOZORDER
    finally:
        if prev:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))
    focus_window(hwnd)
    return x, work.top, w, h


def monitor_count() -> int:
    return user32.GetSystemMetrics(80) if IS_WINDOWS else 1             # SM_CMONITORS


def window_next_monitor(hwnd: int) -> None:
    """Перенести окно на соседний монитор — как Win+Shift+→ (размер и «на весь экран» сохраняются)."""
    import time
    focus_window(hwnd)
    time.sleep(0.2)
    press(VK["win"], VK["shift"], VK["right"])


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


def _com():
    import comtypes
    try:
        comtypes.CoInitialize()        # умения выполняются не в главном потоке
    except OSError:
        pass


def audio_outputs() -> list[dict]:
    """Включённые устройства вывода звука: [{"id", "name", "default"}]."""
    _need_windows()
    _com()
    from pycaw.pycaw import AudioUtilities
    try:
        cur_id = getattr(AudioUtilities.GetSpeakers(), "id", None)
    except Exception:              # «Element not found» — устройства по умолчанию нет (нет звуковой карты)
        cur_id = None
    out = []
    for d in AudioUtilities.GetAllDevices(data_flow=0, device_state=1):    # eRender, DEVICE_STATE_ACTIVE
        if d is not None and d.FriendlyName:
            out.append({"id": d.id, "name": d.FriendlyName, "default": d.id == cur_id})
    return out


def set_audio_output(dev_id: str) -> None:
    """Сделать устройство выводом по умолчанию — для всего (музыка, игры, звонки)."""
    _need_windows()
    _com()
    from pycaw.constants import ERole
    from pycaw.pycaw import AudioUtilities
    AudioUtilities.SetDefaultDevice(dev_id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])


def app_audio(pids: set[int]) -> list:
    """Громкость звука программ (микшер громкости): ISimpleAudioVolume их сессий."""
    _need_windows()
    _com()
    from pycaw.pycaw import AudioUtilities
    return [s.SimpleAudioVolume for s in AudioUtilities.GetAllSessions() if s.ProcessId in pids]


def get_volume() -> int:
    return round(_endpoint_volume().GetMasterVolumeLevelScalar() * 100)


def set_volume(percent: int) -> None:
    ev = _endpoint_volume()
    ev.SetMute(0, None)
    ev.SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100, None)


# ——— PowerShell (то, до чего из Python не дотянуться без новых библиотек) ———

def powershell(script: str, timeout: float = 30) -> str:
    """Выполнить сценарий в Windows PowerShell 5.1 без окна; вывод — UTF-8. Ошибка без вывода — исключение."""
    _need_windows()
    script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; " \
             + script
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", script], capture_output=True, timeout=timeout,
                           creationflags=0x08000000)                 # CREATE_NO_WINDOW
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"команда не уложилась в {timeout:.0f} с")
    out = p.stdout.decode("utf-8", "replace").strip()
    err = p.stderr.decode("utf-8", "replace").strip()
    if p.returncode != 0 and not out:
        raise RuntimeError(err[:500] or f"код {p.returncode}")
    return out


# ——— экран: яркость, ночной свет, выключить монитор ———

def _ddc_monitors() -> list:
    """Физические мониторы (DDC/CI) — [(dxva2, массив PHYSICAL_MONITOR, число)] для каждого экрана."""
    class PHYSICAL_MONITOR(ctypes.Structure):
        _fields_ = [("h", wintypes.HANDLE), ("desc", wintypes.WCHAR * 128)]

    dxva2 = ctypes.WinDLL("dxva2")
    H, D = wintypes.HANDLE, wintypes.DWORD
    dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [H, ctypes.POINTER(D)]
    dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [H, D, ctypes.c_void_p]
    dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [H, wintypes.BYTE, ctypes.c_void_p, ctypes.POINTER(D),
                                                      ctypes.POINTER(D)]
    dxva2.SetVCPFeature.argtypes = [H, wintypes.BYTE, D]
    dxva2.DestroyPhysicalMonitors.argtypes = [D, ctypes.c_void_p]
    hmons = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                              wintypes.LPARAM)

    def cb(hmon, hdc, rect, _):
        hmons.append(hmon)
        return True

    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, proc, wintypes.LPARAM]
    user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    out = []
    for hmon in hmons:
        n = wintypes.DWORD()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)) or not n.value:
            continue
        arr = (PHYSICAL_MONITOR * n.value)()
        if dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, n.value, arr):
            out.append((dxva2, arr, n.value))
    return out


def _ddc_brightness(set_to=None, step: int = 0) -> list[int]:
    """Яркость внешних мониторов по DDC/CI (код VCP 0x10). Возвращает новые значения в процентах."""
    got = []
    for dxva2, arr, n in _ddc_monitors():
        try:
            for i in range(n):
                h = arr[i].h
                cur, mx = wintypes.DWORD(), wintypes.DWORD()
                if not dxva2.GetVCPFeatureAndVCPFeatureReply(h, 0x10, None, ctypes.byref(cur), ctypes.byref(mx)):
                    continue                          # монитор не отвечает: DDC/CI выключен в его меню
                top = mx.value or 100
                pct = round(cur.value * 100 / top)
                if set_to is not None or step:
                    pct = max(0, min(100, set_to if set_to is not None else pct + step))
                    if not dxva2.SetVCPFeature(h, 0x10, round(pct * top / 100)):
                        continue
                got.append(pct)
        finally:
            dxva2.DestroyPhysicalMonitors(n, arr)
    return got


def _wmi_brightness(set_to=None, step: int = 0) -> list[int]:
    """Яркость встроенного экрана ноутбука (WMI). Возвращает новое значение — или [], если такого экрана нет."""
    try:
        cur = powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
                         "-ErrorAction Stop | Select-Object -First 1).CurrentBrightness", timeout=15)
    except RuntimeError:
        return []
    if not cur.strip().isdigit():
        return []
    pct = int(cur.strip())
    if set_to is None and not step:
        return [pct]
    pct = max(0, min(100, set_to if set_to is not None else pct + step))
    powershell("Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
               f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={pct}}} | "
               "Out-Null", timeout=15)
    return [pct]


def brightness(set_to: int | None = None, step: int = 0) -> list[int]:
    """Узнать или поменять яркость всех экранов: внешние мониторы — DDC/CI, ноутбук — WMI.
    Пустой список — ни один экран яркость из программ не меняет."""
    _need_windows()
    got = []
    try:
        got = _ddc_brightness(set_to, step)
    except OSError:
        pass
    return got + _wmi_brightness(set_to, step)


NIGHT_KEY = (r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
             r"\default$windows.data.bluelightreduction.bluelightreductionstate"
             r"\windows.data.bluelightreduction.bluelightreductionstate")


def night_light_blob(data: bytes, on: bool) -> bytes | None:
    """Новое состояние «ночного света» в формате Windows 10/11. None — формат незнакомый, лучше не трогать.
    Включённый отличается байтом 18 (0x15 вместо 0x13) и двумя байтами 10 00 после 23-го;
    метка времени (байты 10–14) должна измениться, иначе Windows не заметит."""
    d = bytearray(data)
    if len(d) < 30:
        return None
    is_on = d[18] == 0x15
    if d[18] not in (0x13, 0x15) or (is_on and bytes(d[23:25]) != b"\x10\x00"):
        return None
    if is_on == on:
        return bytes(d)
    if on:
        d[18] = 0x15
        d[23:23] = b"\x10\x00"
    else:
        d[18] = 0x13
        del d[23:25]
    for i in range(10, 15):
        if d[i] != 0xFF:
            d[i] += 1
            break
    return bytes(d)


def night_light(on: bool | None = None) -> bool | None:
    """Узнать (on=None) или поменять «ночной свет». Возвращает новое состояние; None — поменять не вышло."""
    _need_windows()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NIGHT_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            data, kind = winreg.QueryValueEx(k, "Data")
            if on is None:
                new = night_light_blob(data, True)
                return None if new is None else data[18] == 0x15
            new = night_light_blob(data, on)
            if new is None:
                return None
            if new != data:
                winreg.SetValueEx(k, "Data", 0, kind, new)
            return on
    except OSError:
        return None                                   # ночной свет ни разу не открывали — ключа ещё нет


def monitor_off() -> None:
    """Погасить экраны (компьютер продолжает работать); включаются от мыши, клавиш или «включи экран»."""
    _need_windows()
    user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)                # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER


def monitor_on() -> None:
    _need_windows()
    user32.mouse_event(0x0001, 1, 0, 0, 0)                         # MOUSEEVENTF_MOVE: сдвиг на пиксель и назад
    user32.mouse_event(0x0001, -1, 0, 0, 0)


# ——— файлы: выделенное в проводнике, корзина ———

def explorer_selection(foreground_only: bool = False) -> list[str]:
    """Пути файлов, выделенных в окне проводника, с которым работают (или в верхнем окне проводника;
    foreground_only — только если проводник и есть то окно, с которым работают)."""
    _need_windows()
    _com()
    import comtypes.client
    order = [w["hwnd"] for w in open_windows() if w["exe"].lower() == "explorer.exe"]
    fg = foreground_window()
    if fg and fg["hwnd"] in order:
        order.remove(fg["hwnd"])
        order.insert(0, fg["hwnd"])
    elif foreground_only:
        return []
    if not order:
        return []
    shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
    wins = shell.Windows()
    by_hwnd = {}
    for i in range(wins.Count):
        try:
            w = wins.Item(i)
            by_hwnd[int(w.HWND)] = w
        except Exception:
            continue
    for hwnd in order:
        w = by_hwnd.get(hwnd)
        if w is None:
            continue
        try:
            items = w.Document.SelectedItems()
            return [items.Item(i).Path for i in range(items.Count)]
        except Exception:
            return []
    return []


def recycle(paths: list[str]) -> bool:
    """Отправить файлы и папки в корзину (их можно восстановить). True — получилось."""
    _need_windows()

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", wintypes.WORD), ("aborted", wintypes.BOOL),
                    ("mappings", ctypes.c_void_p), ("title", ctypes.c_void_p)]

    buf = ctypes.create_unicode_buffer("\0".join(os.path.abspath(p) for p in paths) + "\0")
    op = SHFILEOPSTRUCTW(None, 3, ctypes.addressof(buf), None,       # FO_DELETE
                         0x40 | 0x10 | 0x04 | 0x400)                 # ALLOWUNDO, NOCONFIRMATION, SILENT, NOERRORUI
    return shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.aborted


def recycle_bin() -> tuple[int, int]:
    """Сколько в корзине: (файлов, байт)."""
    _need_windows()

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("size", ctypes.c_longlong), ("count", ctypes.c_longlong)]

    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO))
    if shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0, 0
    return info.count, info.size


def empty_recycle_bin() -> None:
    _need_windows()
    shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)         # без вопроса, без окна, без звука


# ——— сеть: Wi‑Fi и Bluetooth (Windows.Devices.Radios — без прав администратора) ———

_RADIOS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
  $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus, Windows.System.Devices, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState, Windows.System.Devices, ContentType=WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
"""


def radios(kind: str | None = None, state: str | None = None) -> dict[str, str]:
    """Радиомодули: {"WiFi": "On", "Bluetooth": "Off"}. С kind и state («On»/«Off») — сначала переключить."""
    script = _RADIOS
    if kind and state:
        script += (f"$radios | Where-Object {{ $_.Kind -eq '{kind}' }} | ForEach-Object {{ Await "
                   f"($_.SetStateAsync('{state}')) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null }}\n"
                   "Start-Sleep -Milliseconds 500\n")
    script += "$radios | ForEach-Object { \"$($_.Kind)=$($_.State)\" }"
    out = {}
    for line in powershell(script).splitlines():
        k, _, v = line.strip().partition("=")
        if k and v and k not in out:
            out[k] = v
    return out


def connections() -> list[dict]:
    """Подключения: [{"alias": «Wi-Fi», "name": имя сети, "internet": bool}]."""
    out = []
    script = ("Get-NetConnectionProfile | ForEach-Object { "
              "\"$($_.InterfaceAlias)|$($_.Name)|$($_.IPv4Connectivity)|$($_.IPv6Connectivity)\" }")
    for line in powershell(script, timeout=15).splitlines():
        parts = line.strip().split("|")
        if len(parts) == 4:
            out.append({"alias": parts[0], "name": parts[1], "internet": "Internet" in parts[2] + parts[3]})
    return out


# ——— раскладка, «не беспокоить», выключение по таймеру ———

LANGS = {"en": 0x09, "ru": 0x19, "uk": 0x22, "de": 0x07, "fr": 0x0C, "es": 0x0A, "be": 0x23, "kk": 0x3F}


def keyboard_layouts() -> list[int]:
    """Установленные раскладки (HKL)."""
    _need_windows()
    user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    n = user32.GetKeyboardLayoutList(0, None)
    arr = (ctypes.c_void_p * n)()
    user32.GetKeyboardLayoutList(n, arr)
    return [a or 0 for a in arr]


def current_layout(hwnd: int | None = None) -> int:
    """Язык раскладки окна (младший байт: 0x09 — английский, 0x19 — русский)."""
    _need_windows()
    user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    user32.GetKeyboardLayout.restype = ctypes.c_void_p
    hwnd = hwnd or user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    return (user32.GetKeyboardLayout(tid) or 0) & 0xFF


def switch_layout(hwnd: int, hkl: int | None = None) -> None:
    """Сменить раскладку в окне: на hkl или следующую (как Alt+Shift)."""
    _need_windows()
    lparam = ctypes.c_ssize_t(hkl).value if hkl is not None else 1          # HKL_NEXT
    user32.PostMessageW(hwnd, 0x0050, 0, lparam)                            # WM_INPUTLANGCHANGEREQUEST


_QUIET = ctypes.c_ulonglong(0x0D83063EA3BF1C75) if IS_WINDOWS else None   # WNF_SHEL_QUIETHOURS_ACTIVE_PROFILE_CHANGED


def focus_assist(mode: int | None = None) -> int | None:
    """«Не беспокоить» (фокусировка внимания): 0 — выкл., 1 — только важное, 2 — только будильники.
    mode=None — узнать. Возвращает режим после действия или None, если Windows не ответила."""
    _need_windows()
    ntdll = ctypes.WinDLL("ntdll")
    if mode is not None:
        val = wintypes.DWORD(mode)
        ntdll.NtUpdateWnfStateData(ctypes.byref(_QUIET), ctypes.byref(val), 4, None, None, 0, 0)
    stamp, size, buf = wintypes.ULONG(), wintypes.ULONG(4), wintypes.DWORD()
    if ntdll.NtQueryWnfStateData(ctypes.byref(_QUIET), None, None, ctypes.byref(stamp),
                                 ctypes.byref(buf), ctypes.byref(size)) != 0:
        return None
    return buf.value


def shutdown_in(seconds: int) -> bool:
    """Запланировать выключение (Windows покажет уведомление). False — уже что-то запланировано."""
    _need_windows()
    p = subprocess.run(["shutdown", "/s", "/t", str(max(0, int(seconds)))], capture_output=True,
                       creationflags=0x08000000)
    return p.returncode == 0


def shutdown_cancel() -> bool:
    """Отменить запланированное выключение. False — ничего не было запланировано."""
    _need_windows()
    p = subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=0x08000000)
    return p.returncode == 0
