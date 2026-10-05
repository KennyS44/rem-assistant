"""Внешний вид: значок, своя картинка, голубая тема окон и карточка ответа.

Значок по умолчанию — собственный рисунок «по мотивам» (rem/art/icon.png, исходник
packaging/art/icon.svg): его можно раздавать вместе с программой. Свою картинку
(например, лицо Рем) человек выбирает сам; она остаётся только у него —
%APPDATA%\\Rem\\avatar.png — и никуда не выкладывается.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

from .config import app_dir

log = logging.getLogger("rem.look")

ART = Path(__file__).parent / "art"

STATE_COLORS = {
    "idle": "#4f7dd9", "listening": "#2fb36e", "thinking": "#e0a72e",
    "paused": "#8a8f98", "game": "#9b6bd6", "error": "#d9534f",
}

# тема «Рем»: голубой, белый, тёмно-синий. Контраст текста к фону ≥ 4.5:1
BG = "#f3f7fc"
SURFACE = "#ffffff"
LINE = "#cddbee"
TEXT = "#1d2a44"            # 13.6:1 на BG
MUTED = "#536079"           # 6.0:1 на BG
ACCENT = "#3a6bb8"          # белый текст на нём — 5.2:1
ACCENT_DARK = "#2f5ca3"
ACCENT_SOFT = "#e2edfa"
FONT = ("Segoe UI", 10)


# ——— картинки ———

def avatar_path() -> Path:
    return app_dir() / "avatar.png"


def circle(img: Image.Image, size: int = 256) -> Image.Image:
    """Квадрат → круг с ровным краем (маска рисуется вчетверо крупнее)."""
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), ImageChops.multiply(img.getchannel("A"), mask))
    return out


def make_avatar(src) -> Image.Image:
    """Любая картинка → круглый аватар 256×256. У высоких картинок берём верх:
    лицо на артах обычно в верхней части."""
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGBA")
    w, h = img.size
    s = min(w, h)
    left = (w - s) // 2
    top = int((h - s) * 0.2)
    return circle(img.crop((left, top, left + s, top + s)))


def set_avatar(src) -> Path:
    make_avatar(src).save(avatar_path())
    return avatar_path()


def clear_avatar() -> None:
    avatar_path().unlink(missing_ok=True)


def has_avatar() -> bool:
    return avatar_path().exists()


def face() -> Image.Image:
    """Своя картинка, если выбрана, иначе значок Рэм."""
    if has_avatar():
        try:
            return Image.open(avatar_path()).convert("RGBA")
        except Exception as e:                       # файл испорчен — не мешаем запуску
            log.warning("своя картинка не открылась: %s", e)
    return Image.open(ART / "icon.png").convert("RGBA")


def tray_image(state: str, base: Image.Image) -> Image.Image:
    """Значок в трее: лицо + цветная точка состояния (в покое точки нет)."""
    img = base.resize((64, 64), Image.LANCZOS)
    if state == "paused":
        gray = ImageOps.grayscale(img).convert("RGBA")
        gray.putalpha(img.getchannel("A"))
        img = gray
    if state != "idle":
        d = ImageDraw.Draw(img)
        d.ellipse((38, 38, 63, 63), fill="white")
        d.ellipse((42, 42, 59, 59), fill=STATE_COLORS.get(state, STATE_COLORS["idle"]))
    return img


# ——— тема окон ———

def apply_theme(root, on: bool) -> None:
    """Голубая тема для всех окон ttk. Выключена — обычный вид Windows."""
    from tkinter import ttk
    style = ttk.Style(root)
    root.option_clear()
    root.option_add("*Font", FONT)
    if not on:
        style.theme_use("vista" if "vista" in style.theme_names() else "default")
        return
    style.theme_use("clam")
    for pattern, value in (("*Toplevel.background", BG), ("*Canvas.background", BG),
                           ("*TCombobox*Listbox.background", SURFACE),
                           ("*TCombobox*Listbox.foreground", TEXT),
                           ("*TCombobox*Listbox.selectBackground", ACCENT),
                           ("*TCombobox*Listbox.selectForeground", "white")):
        root.option_add(pattern, value)
    root.configure(background=BG)
    style.configure(".", background=BG, foreground=TEXT, font=FONT, bordercolor=LINE,
                    lightcolor=SURFACE, darkcolor=LINE, troughcolor=ACCENT_SOFT, focuscolor=ACCENT,
                    selectbackground=ACCENT, selectforeground="white", fieldbackground=SURFACE,
                    insertcolor=TEXT, arrowcolor=ACCENT)
    style.map(".", foreground=[("disabled", "#8d97a8")])

    style.configure("TButton", background=SURFACE, padding=(12, 4), focusthickness=1)
    style.map("TButton", background=[("pressed", ACCENT_SOFT), ("active", ACCENT_SOFT)],
              bordercolor=[("focus", ACCENT), ("active", ACCENT)],
              lightcolor=[("active", ACCENT_SOFT)], darkcolor=[("active", ACCENT_SOFT)])
    style.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT)
    style.map("Accent.TButton", background=[("pressed", ACCENT_DARK), ("active", ACCENT_DARK)],
              lightcolor=[("active", ACCENT_DARK)], darkcolor=[("active", ACCENT_DARK)],
              bordercolor=[("active", ACCENT_DARK)])

    style.configure("TNotebook", background=BG, bordercolor=LINE, tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(12, 4),
                    bordercolor=LINE, lightcolor=BG)
    style.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", ACCENT_SOFT)],
              foreground=[("selected", ACCENT)], lightcolor=[("selected", SURFACE)])

    _checkbox(root, style)
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(w, padding=4, lightcolor=SURFACE)
        style.map(w, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                  fieldbackground=[("readonly", SURFACE)], selectbackground=[("readonly", SURFACE)],
                  selectforeground=[("readonly", TEXT)])
    style.configure("Horizontal.TScale", background=ACCENT, troughcolor=ACCENT_SOFT,
                    bordercolor=ACCENT_SOFT, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=ACCENT_SOFT,
                    bordercolor=ACCENT_SOFT, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, bordercolor=LINE)
    style.configure("Treeview.Heading", background=BG, foreground=MUTED, bordercolor=LINE)
    style.configure("TSeparator", background=LINE)
    style.configure("Vertical.TScrollbar", background=ACCENT_SOFT, troughcolor=BG, bordercolor=BG,
                    lightcolor=ACCENT_SOFT, darkcolor=ACCENT_SOFT)


def _check_image(on: bool, border: str, fill: str) -> Image.Image:
    """Галочка 18×18 (и 6 px отступа до подписи), нарисованная вчетверо крупнее."""
    k = 4
    img = Image.new("RGBA", (24 * k, 18 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((k, k, 17 * k, 17 * k), radius=4 * k, fill=fill, outline=border, width=int(1.5 * k))
    if on:
        d.line([(5 * k, 9 * k), (8 * k, 12 * k), (13 * k, 6 * k)], fill="white", width=2 * k, joint="curve")
    return img.resize((24, 18), Image.LANCZOS)


def _checkbox(root, style) -> None:
    from PIL import ImageTk
    if "Rem.check" not in style.element_names():
        imgs = [ImageTk.PhotoImage(_check_image(*a), master=root) for a in (
            (False, "#7f95b5", SURFACE), (True, ACCENT, ACCENT), (False, ACCENT, ACCENT_SOFT),
            (True, ACCENT_DARK, ACCENT_DARK), (False, LINE, BG), (True, LINE, LINE))]
        root._rem_check = imgs                   # иначе Tk потеряет картинки
        off, on, off_hover, on_hover, off_dis, on_dis = imgs
        style.element_create("Rem.check", "image", off,
                             ("disabled", "selected", on_dis), ("disabled", off_dis),
                             ("active", "selected", on_hover), ("selected", on), ("active", off_hover))
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Rem.check", {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
            ("Checkbutton.label", {"sticky": "nswe"})]})]})])
    style.configure("TCheckbutton", padding=(0, 2))
    style.map("TCheckbutton", background=[("active", BG)])


# ——— карточка ответа ———

def work_area() -> tuple[int, int, int, int] | None:
    """Рабочая область основного экрана без панели задач (Windows)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    if _user32().SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):   # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    return None


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def card_seconds(text: str) -> float:
    """Сколько держать карточку: успеть прочитать, но не висеть вечно."""
    return max(4.0, min(12.0, 2.0 + len(text) / 15))


class Card:
    """Окошко у правого нижнего угла: лицо Рэм и текст. Не забирает фокус у окна,
    в котором человек работает, и не появляется поверх полноэкранных игр и видео."""

    WIDTH = 360
    MARGIN = 16

    def __init__(self, root, photo):
        import tkinter as tk
        self.root = root
        self.win = w = tk.Toplevel(root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(background=LINE)
        body = tk.Frame(w, background=SURFACE, padx=12, pady=12)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        self.pic = tk.Label(body, image=photo, background=SURFACE)
        self.pic.pack(side="left", anchor="n")
        col = tk.Frame(body, background=SURFACE)
        col.pack(side="left", fill="both", expand=True, padx=(12, 0))
        tk.Label(col, text="Рэм", font=("Segoe UI Semibold", 10), foreground=ACCENT,
                 background=SURFACE).pack(anchor="w")
        self.text = tk.Label(col, text="", font=("Segoe UI", 11), foreground=TEXT, background=SURFACE,
                             justify="left", anchor="w", wraplength=self.WIDTH - 100)
        self.text.pack(anchor="w", fill="x")
        for widget in (w, body, self.pic, col, self.text):
            widget.bind("<Button-1>", lambda e: self.hide())
        self.alpha = 0.0
        self._anim = None
        self._timer = None
        self._styled = False

    def set_photo(self, photo) -> None:
        self.pic.configure(image=photo)

    def show(self, text: str, seconds: float | None = None) -> bool:
        """seconds=None — держать, пока не скажут hide(). False — не показали (игра, видео)."""
        from . import winapi
        try:
            if winapi.foreground_is_fullscreen():
                return False
        except Exception:
            pass
        self.text.configure(text=text)
        w = self.win
        w.update_idletasks()
        h = w.winfo_reqheight()
        area = work_area() or (0, 0, w.winfo_screenwidth(), w.winfo_screenheight() - 48)
        x = area[2] - self.WIDTH - self.MARGIN
        y = area[3] - h - self.MARGIN
        w.geometry(f"{self.WIDTH}x{h}+{x}+{y}")
        if w.state() == "withdrawn":
            prev = _foreground()
            w.attributes("-alpha", self.alpha)
            w.deiconify()
            self._no_activate(prev)
        self._fade(0.97)
        if self._timer:
            self.root.after_cancel(self._timer)
            self._timer = None
        if seconds:
            self._timer = self.root.after(int(seconds * 1000), self.hide)
        return True

    def hide(self) -> None:
        if self._timer:
            self.root.after_cancel(self._timer)
            self._timer = None
        if self.win.state() != "withdrawn":
            self._fade(0.0, then=self.win.withdraw)

    def visible(self) -> bool:
        return self.win.state() != "withdrawn"

    def _fade(self, target: float, then=None, ms: int = 180) -> None:
        if self._anim:
            self.root.after_cancel(self._anim)
        start, steps = self.alpha, max(1, ms // 15)

        def step(i=1):
            self.alpha = start + (target - start) * ease_out(i / steps)
            self.win.attributes("-alpha", self.alpha)
            if i < steps:
                self._anim = self.root.after(15, step, i + 1)
            else:
                self._anim = None
                if then:
                    then()
        step()

    def _no_activate(self, prev) -> None:
        """Окно-подсказка: без кнопки на панели задач и без перехвата фокуса.
        Если Windows всё же сделала карточку активной — возвращаем фокус обратно."""
        if sys.platform != "win32":
            return
        user32 = _user32()
        hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        if not self._styled:
            GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = -20, 0x80, 0x08000000
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
            self._styled = True
        if prev and user32.GetForegroundWindow() == hwnd:
            user32.SetForegroundWindow(prev)


def _foreground():
    return _user32().GetForegroundWindow() if sys.platform == "win32" else None


_U32 = None


def _user32():
    """Своя копия user32 с типами HWND (общий ctypes.windll настраивают и другие библиотеки)."""
    global _U32
    if _U32 is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32")
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = wintypes.HWND
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        u.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        _U32 = u
    return _U32


# ——— проверка на сервере сборки ———

def look_test(report: str | None = None, shots: str | None = None) -> int:
    """Значок, своя картинка, тема и карточка — на настоящих окнах Tk.
    shots — папка для снимков окон (их смотрят глазами в артефактах сборки)."""
    import tempfile
    import tkinter as tk
    import types

    ok = True
    if report:
        Path(report).write_text("", encoding="utf-8")

    def check(name, cond):
        nonlocal ok
        line = ("OK   " if cond else "FAIL ") + name
        print(line)
        if report:
            with open(report, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        ok = ok and bool(cond)

    with tempfile.TemporaryDirectory() as d:
        tall = Path(d) / "tall.png"
        Image.new("RGB", (300, 600), "#d04060").save(tall)
        a = make_avatar(tall)
        check("своя картинка → круг 256×256",
              a.size == (256, 256) and a.getpixel((0, 0))[3] == 0 and a.getpixel((128, 128))[3] == 255)
    base = face()
    check("значок Рэм на месте", base.size == (256, 256))
    trays = {s: tray_image(s, base) for s in STATE_COLORS}
    check("значки состояний различаются", len({t.tobytes() for t in trays.values()}) == len(trays))

    from . import config as cfgmod
    from .ui import UI, SettingsWindow
    from .update import can_update  # noqa: F401  (окно настроек спрашивает)
    client = types.SimpleNamespace(available=lambda: False)
    app = types.SimpleNamespace(
        config=dict(cfgmod.DEFAULTS), state="idle", voice=None, listener=None, speakers=None,
        pending_update=None, test_mode=False, log=log,
        assistant=types.SimpleNamespace(client=client, journal=app_dir() / "actions.log", by_name={}))
    ui = UI(app)
    root = ui.root
    out = Path(shots) if shots else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    def settle(ms=400):
        end = root.tk.call("clock", "milliseconds") + ms
        while root.tk.call("clock", "milliseconds") < end:
            root.update()

    def snap(win, name):
        if not out:
            return
        from PIL import ImageGrab
        win.update_idletasks()
        x, y = win.winfo_rootx(), win.winfo_rooty()
        ImageGrab.grab((x - 1, y - 1, x + win.winfo_width() + 1, y + win.winfo_height() + 1)).save(out / name)

    try:
        s = SettingsWindow(ui)
        s.win.geometry("+40+40")
        settle()
        nb = next(w for w in s.win.winfo_children() if w.winfo_class() == "TNotebook")
        tabs = nb.tabs()
        check(f"окно настроек в теме Рем ({len(tabs)} вкладок)",
              ttk_theme(root) == "clam" and "Оформление" in [nb.tab(t, "text") for t in tabs])
        for i, t in enumerate(tabs):
            nb.select(t)
            settle(250)
            snap(s.win, f"settings-{i + 1}.png")

        # карточка не должна забирать фокус у окна, в котором человек работает
        focus = tk.Toplevel(root)
        focus.title("Окно человека")
        focus.geometry("420x200+60+60")
        tk.Entry(focus).pack(padx=16, pady=16)
        s.win.destroy()
        focus.lift()
        focus.focus_force()
        settle()
        before = _foreground()
        shown = ui._card().show("Рэм слушает. Таймер на 5 минут поставлен.", 6)
        settle(600)
        check("карточка показана", shown and ui.card.visible())
        check("фокус остался у окна человека", sys.platform != "win32" or _foreground() == before)
        snap(ui.card.win, "card.png")
        ui._card().show("Сейчас восемь часов пятнадцать минут. А ещё длинный ответ, который не "
                        "помещается в одну строку и переносится.", 6)
        settle(400)
        check("фокус остался и при втором ответе", sys.platform != "win32" or _foreground() == before)
        snap(ui.card.win, "card-long.png")
        ui.card.hide()
        settle(400)
        check("карточка прячется", not ui.card.visible())

        app.config["rem_theme"] = False
        ui.apply_look()
        check("обычный вид Windows включается", ttk_theme(root) != "clam")
    except Exception as e:
        log.exception("проверка оформления")
        check(f"окна: {e!r}", False)
    finally:
        root.destroy()
    line = "ИТОГ: " + ("всё в порядке" if ok else "есть ошибки")
    print(line)
    if report:
        with open(report, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return 0 if ok else 1


def ttk_theme(root) -> str:
    from tkinter import ttk
    return ttk.Style(root).theme_use()
