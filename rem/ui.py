"""Интерфейс: значок в трее, окно настроек, окно первого запуска, проверка слова активации.

Tkinter живёт в главном потоке; значок в трее и слух — в своих потоках.
Всё, что трогает окна из чужих потоков, идёт через ui.call() → root.after().
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw

from . import autostart, config as cfgmod
from .skills import REGISTRY

STATE_COLORS = {
    "idle": "#4f7dd9", "listening": "#2fb36e", "thinking": "#e0a72e",
    "paused": "#8a8f98", "game": "#9b6bd6", "error": "#d9534f",
}
STATE_TEXT = {
    "idle": "слушаю", "listening": "слышу команду", "thinking": "выполняю",
    "paused": "на паузе", "game": "игровой режим", "error": "ошибка",
}

PAD = 8
FONT = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI Semibold", 12)


def tray_image(state: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=STATE_COLORS.get(state, "#4f7dd9"))
    d.ellipse((24, 24, 40, 40), fill="white")
    return img


class UI:
    def __init__(self, app):
        self.app = app                          # rem.__main__.App
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Рэм")
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self.root.option_add("*Font", FONT)
        self.calls: queue.Queue = queue.Queue()
        self.icon = None
        self.settings_win = None
        self.root.after(50, self._pump)

    # ——— межпоточные вызовы ———

    def call(self, fn, *args) -> None:
        self.calls.put((fn, args))

    def _pump(self) -> None:
        while not self.calls.empty():
            fn, args = self.calls.get_nowait()
            try:
                fn(*args)
            except Exception as e:                      # окно не должно ронять помощника
                self.app.log.exception("ошибка интерфейса: %s", e)
        self.root.after(50, self._pump)

    # ——— трей ———

    def start_tray(self) -> None:
        import pystray
        m = pystray.MenuItem
        menu = pystray.Menu(
            m(lambda _: f"Рэм — {STATE_TEXT.get(self.app.state, '')}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            m(lambda _: "Продолжить слушать" if self.app.state == "paused" else "Не слушать",
              lambda: self.app.toggle_pause()),
            m("Настройки…", lambda: self.call(self.open_settings), default=True),
            m("Проверить слово активации…", lambda: self.call(self.open_wake_test)),
            m("Журнал команд", lambda: self._open(self.app.assistant.journal)),
            m("Папка с настройками", lambda: self._open(cfgmod.app_dir())),
            pystray.Menu.SEPARATOR,
            m("Выход", lambda: self.call(self.app.quit)),
        )
        self.icon = pystray.Icon("Rem", tray_image("idle"), "Рэм", menu)
        self.icon.run_detached()

    def set_state(self, state: str) -> None:
        if self.icon:
            self.icon.icon = tray_image(state)
            self.icon.title = f"Рэм — {STATE_TEXT.get(state, '')}"
            self.icon.update_menu()

    def notify(self, text: str) -> None:
        if self.icon:
            try:
                self.icon.notify(text, "Рэм")
            except Exception:
                pass

    @staticmethod
    def _open(path) -> None:
        if os.name == "nt":
            os.startfile(str(path))

    # ——— окно первого запуска ———

    def progress_window(self, title: str):
        """Окно с полосой прогресса. Возвращает (окно, update(доля, текст), close())."""
        w = tk.Toplevel(self.root)
        w.title("Рэм — подготовка")
        w.resizable(False, False)
        f = ttk.Frame(w, padding=16)
        f.pack(fill="both")
        ttk.Label(f, text=title, font=FONT_TITLE).pack(anchor="w")
        lbl = ttk.Label(f, text="", width=52)
        lbl.pack(anchor="w", pady=(PAD, 4))
        bar = ttk.Progressbar(f, length=400, maximum=1000)
        bar.pack(fill="x")
        w.protocol("WM_DELETE_WINDOW", lambda: None)

        def update(frac: float, text: str = "") -> None:
            def go():
                bar["value"] = int(frac * 1000)
                if text:
                    lbl["text"] = f"{text} — {int(frac * 100)}%"
            self.call(go)

        return w, update, lambda: self.call(w.destroy)

    def ask_ollama(self) -> bool:
        """Ollama не найдена: объясняем и даём ссылку. True — попробовать ещё раз."""
        ans = messagebox.askyesnocancel(
            "Рэм — нужна Ollama",
            "Для понимания команд Рэм использует локальную модель через программу Ollama, "
            "но она не запущена.\n\n«Да» — открыть страницу загрузки Ollama (установи её и вернись).\n"
            "«Нет» — проверить ещё раз.\n«Отмена» — работать только с простыми командами.")
        if ans:
            import webbrowser
            webbrowser.open("https://ollama.com/download/windows")
            messagebox.showinfo("Рэм", "Когда установка Ollama закончится, нажми «ОК».")
            return True
        return ans is False

    # ——— проверка слова активации ———

    def open_wake_test(self) -> None:
        w = tk.Toplevel(self.root)
        w.title("Рэм — проверка слова активации")
        w.resizable(False, False)
        f = ttk.Frame(w, padding=16)
        f.pack(fill="both")
        wake = self.app.config["wake_word"].capitalize()
        ttk.Label(f, text="Проверка слова активации", font=FONT_TITLE).pack(anchor="w")
        ttk.Label(f, wraplength=420, justify="left",
                  text=f"Скажи «{wake}» 10 раз, делая паузу 2–3 секунды после каждого. "
                       f"Команды в это время не выполняются.").pack(anchor="w", pady=(PAD, 12))
        counter = ttk.Label(f, text="Услышано: 0 из 10", font=FONT_TITLE)
        counter.pack(anchor="w")
        detail = ttk.Label(f, text="", foreground="#555")
        detail.pack(anchor="w", pady=(4, 12))
        start = dict(self.app.listener.stats)
        self.app.test_mode = True

        def tick():
            if not w.winfo_exists():
                return
            s = self.app.listener.stats
            acc = s["accepted"] - start["accepted"]
            rej = s["rejected"] - start["rejected"]
            counter["text"] = f"Услышано: {acc} из 10"
            detail["text"] = f"Сторож отреагировал и проверка отсеяла: {rej}"
            if acc >= 10:
                counter["text"] = "Отлично: 10 из 10"
            w.after(300, tick)

        def close():
            self.app.test_mode = False
            w.destroy()

        ttk.Button(f, text="Готово", command=close).pack(anchor="e")
        w.protocol("WM_DELETE_WINDOW", close)
        tick()

    # ——— настройки ———

    def open_settings(self) -> None:
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        SettingsWindow(self)


class SettingsWindow:
    GAME_MODES = {"fast_only": "только простые команды", "cpu": "всё, но медленно (на процессоре)",
                  "off": "не отслеживать игры"}

    def __init__(self, ui: UI):
        self.ui = ui
        self.app = ui.app
        self.cfg = dict(self.app.config)
        self.custom = [dict(c) for c in self.cfg.get("custom_skills", [])]
        w = self.win = tk.Toplevel(ui.root)
        ui.settings_win = w
        w.title("Рэм — настройки")
        w.minsize(560, 520)
        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        nb.add(self._general(nb), text="Основное")
        nb.add(self._skills(nb), text="Умения")
        nb.add(self._custom_tab(nb), text="Мои умения")
        nb.add(self._try_tab(nb), text="Проверка")
        bar = ttk.Frame(w, padding=(PAD, 0, PAD, PAD))
        bar.pack(fill="x")
        ttk.Button(bar, text="Сохранить", command=self.save).pack(side="right")
        ttk.Button(bar, text="Отмена", command=w.destroy).pack(side="right", padx=PAD)

    # вкладка «Основное»
    def _general(self, nb):
        f = ttk.Frame(nb, padding=16)
        f.columnconfigure(1, weight=1)
        r = 0

        def row(label, widget, hint=""):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 12))
            widget.grid(row=r, column=1, sticky="ew", pady=4)
            r += 1
            if hint:
                ttk.Label(f, text=hint, foreground="#666", wraplength=380, justify="left").grid(
                    row=r, column=1, sticky="w", pady=(0, 8))
                r += 1

        self.wake = tk.StringVar(value=self.cfg["wake_word"])
        wf = ttk.Frame(f)
        ttk.Entry(wf, textvariable=self.wake, width=20).pack(side="left")
        self.wake_status = ttk.Label(wf, text="")
        self.wake_status.pack(side="left", padx=PAD)
        self.wake.trace_add("write", lambda *_: self._check_wake())
        row("Слово активации", wf, "Лучше всего работают слова из 2–3 слогов с необычным звучанием.")
        self._check_wake()

        self.model = tk.StringVar(value=self.cfg["model"])
        row("Модель Ollama", ttk.Entry(f, textvariable=self.model))
        self.model_status = ttk.Label(f, text="проверяю…", foreground="#666")
        self.model_status.grid(row=r, column=1, sticky="w", pady=(0, 8))
        r += 1
        threading.Thread(target=self._probe_model, daemon=True).start()

        self.keep = tk.IntVar(value=int(self.cfg.get("keep_alive_min", 3)))
        row("Держать модель в видеопамяти", ttk.Spinbox(f, from_=1, to=30, textvariable=self.keep, width=6),
            "Минут после последней команды. Потом видеопамять освобождается.")

        self.game = tk.StringVar(value=self.GAME_MODES[self.cfg.get("game_mode", "fast_only")])
        row("Во время игр", ttk.Combobox(f, textvariable=self.game, state="readonly",
                                         values=list(self.GAME_MODES.values())))

        self.engine = tk.StringVar(value=self.cfg.get("search_engine", "google"))
        row("Поиск", ttk.Combobox(f, textvariable=self.engine, state="readonly", values=["google", "yandex"]))

        self.speak = tk.BooleanVar(value=self.cfg.get("speak_replies", True))
        row("", ttk.Checkbutton(f, text="Отвечать голосом", variable=self.speak))
        self.auto = tk.BooleanVar(value=autostart.is_enabled())
        row("", ttk.Checkbutton(f, text="Запускать вместе с Windows", variable=self.auto))
        return f

    def _check_wake(self):
        w = self.wake.get().strip().lower()
        if not w:
            self.wake_status.config(text="введи слово", foreground="#b33")
        elif " " in w:
            self.wake_status.config(text="нужно одно слово", foreground="#b33")
        elif self.app.listener and not self.app.listener.is_word(w):
            self.wake_status.config(text="✗ модель не знает этого слова", foreground="#b33")
        else:
            self.wake_status.config(text="✓ подходит", foreground="#2a7")

    def _probe_model(self):
        cl = self.app.assistant.client
        try:
            if not cl.available():
                text = "Ollama не запущена"
            elif self.model.get() not in cl.models():
                text = "модель не скачана — скачаю при сохранении"
            else:
                loaded = [m for m in cl.loaded() if m.get("name") == self.model.get()]
                if loaded:
                    on_gpu = loaded[0].get("size_vram", 0) > 0
                    text = "загружена на видеокарте" if on_gpu else "загружена в память (на процессоре)"
                else:
                    text = "скачана, сейчас не загружена (видеопамять свободна)"
        except Exception as e:
            text = f"ошибка: {e}"
        self.ui.call(lambda: self.model_status.winfo_exists() and self.model_status.config(text=text))

    # вкладка «Умения»
    def _skills(self, nb):
        outer = ttk.Frame(nb, padding=16)
        ttk.Label(outer, text="Выключенные умения Рэм не будет использовать. «Спрашивать» — "
                              "переспросит «да/нет» перед выполнением.", wraplength=500,
                  justify="left").pack(anchor="w", pady=(0, PAD))
        canvas = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        disabled = set(self.cfg.get("disabled_skills", []))
        overrides = self.cfg.get("confirm_overrides", {})
        self.skill_vars = {}
        ttk.Label(inner, text="Вкл.").grid(row=0, column=0, padx=4)
        ttk.Label(inner, text="Умение").grid(row=0, column=1, sticky="w")
        ttk.Label(inner, text="Спрашивать").grid(row=0, column=2, padx=12)
        for i, s in enumerate(sorted(REGISTRY.values(), key=lambda s: (s.category, s.title)), 1):
            on = tk.BooleanVar(value=s.name not in disabled)
            ask = tk.BooleanVar(value=overrides.get(s.name, s.confirm))
            ttk.Checkbutton(inner, variable=on).grid(row=i, column=0)
            ttk.Label(inner, text=f"{s.title}  ·  {s.category}").grid(row=i, column=1, sticky="w")
            ttk.Checkbutton(inner, variable=ask).grid(row=i, column=2)
            self.skill_vars[s.name] = (on, ask, s.confirm)
        return outer

    # вкладка «Мои умения»
    def _custom_tab(self, nb):
        f = ttk.Frame(nb, padding=16)
        ttk.Label(f, wraplength=500, justify="left",
                  text="Свои команды без программирования: открыть программу, файл, папку или сайт, "
                       "либо нажать сочетание клавиш. Фразы перечислять не обязательно — "
                       "модель сама поймёт разные формулировки.").pack(anchor="w", pady=(0, PAD))
        self.tree = ttk.Treeview(f, columns=("type", "target"), show="tree headings", height=10)
        self.tree.heading("#0", text="Название")
        self.tree.heading("type", text="Тип")
        self.tree.heading("target", text="Что делает")
        self.tree.column("type", width=110)
        self.tree.pack(fill="both", expand=True)
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(PAD, 0))
        ttk.Button(b, text="Добавить…", command=lambda: self._edit_custom(None)).pack(side="left")
        ttk.Button(b, text="Изменить…", command=self._edit_selected).pack(side="left", padx=PAD)
        ttk.Button(b, text="Удалить", command=self._delete_selected).pack(side="left")
        self._refresh_custom()
        return f

    def _refresh_custom(self):
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.custom):
            kind = "клавиши" if c.get("type") == "keys" else "открыть"
            self.tree.insert("", "end", iid=str(i), text=c.get("title", ""), values=(kind, c.get("target", "")))

    def _edit_selected(self):
        sel = self.tree.selection()
        if sel:
            self._edit_custom(int(sel[0]))

    def _delete_selected(self):
        sel = self.tree.selection()
        if sel and messagebox.askyesno("Рэм", "Удалить это умение?", parent=self.win):
            del self.custom[int(sel[0])]
            self._refresh_custom()

    def _edit_custom(self, idx):
        item = dict(self.custom[idx]) if idx is not None else {"type": "open"}
        d = tk.Toplevel(self.win)
        d.title("Моё умение")
        d.transient(self.win)
        f = ttk.Frame(d, padding=16)
        f.pack(fill="both")
        f.columnconfigure(1, weight=1)
        title = tk.StringVar(value=item.get("title", ""))
        kind = tk.StringVar(value="Сочетание клавиш" if item.get("type") == "keys" else "Открыть")
        target = tk.StringVar(value=item.get("target", ""))
        phrases = tk.StringVar(value=item.get("phrases", ""))
        confirm = tk.BooleanVar(value=bool(item.get("confirm")))
        ttk.Label(f, text="Название").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 12))
        ttk.Entry(f, textvariable=title, width=40).grid(row=0, column=1, columnspan=2, sticky="ew")
        ttk.Label(f, text="Например: «Открыть Discord», «Скриншот области»", foreground="#666").grid(
            row=1, column=1, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(f, text="Тип").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=kind, state="readonly",
                     values=["Открыть", "Сочетание клавиш"]).grid(row=2, column=1, sticky="w")
        ttk.Label(f, text="Что открыть / клавиши").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=target).grid(row=3, column=1, sticky="ew")

        def browse():
            p = filedialog.askopenfilename(parent=d)
            if p:
                target.set(os.path.normpath(p))
        ttk.Button(f, text="Обзор…", command=browse).grid(row=3, column=2, padx=(PAD, 0))
        ttk.Label(f, text="Путь, адрес сайта или сочетание вида win+shift+s", foreground="#666").grid(
            row=4, column=1, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(f, text="Фразы (необязательно)").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=phrases).grid(row=5, column=1, columnspan=2, sticky="ew")
        ttk.Label(f, text="Через запятую: «дискорд, дис»", foreground="#666").grid(
            row=6, column=1, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Checkbutton(f, text="Спрашивать подтверждение", variable=confirm).grid(
            row=7, column=1, sticky="w", pady=4)

        def ok():
            t, tg = title.get().strip(), target.get().strip()
            if not t or not tg:
                messagebox.showwarning("Рэм", "Заполни название и что делать.", parent=d)
                return
            new = {"title": t, "type": "keys" if kind.get() == "Сочетание клавиш" else "open",
                   "target": tg, "phrases": phrases.get().strip(), "confirm": confirm.get()}
            if new["type"] == "keys":
                from .skills.custom import parse_keys
                try:
                    parse_keys(tg)
                except ValueError as e:
                    messagebox.showwarning("Рэм", f"Не понимаю сочетание: {e}.", parent=d)
                    return
            if idx is None:
                self.custom.append(new)
            else:
                self.custom[idx] = new
            self._refresh_custom()
            d.destroy()

        bar = ttk.Frame(f)
        bar.grid(row=8, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="Отмена", command=d.destroy).pack(side="right")
        ttk.Button(bar, text="OK", command=ok).pack(side="right", padx=PAD)

    # вкладка «Проверка»
    def _try_tab(self, nb):
        f = ttk.Frame(nb, padding=16)
        ttk.Label(f, wraplength=500, justify="left",
                  text="Напиши команду так, как сказал бы её вслух. Рэм покажет, как её понял, "
                       "ничего не выполняя.").pack(anchor="w", pady=(0, PAD))
        e_var = tk.StringVar()
        row = ttk.Frame(f)
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=e_var)
        entry.pack(side="left", fill="x", expand=True)
        out = tk.Text(f, height=12, wrap="word", relief="flat", background="#f6f7f9", padx=8, pady=8)
        out.pack(fill="both", expand=True, pady=(PAD, 0))

        def run():
            text = e_var.get().strip()
            if not text:
                return
            out.delete("1.0", "end")
            out.insert("end", "думаю…")

            def work():
                plan = self.app.assistant.plan(text)
                lines = [f"Источник: {'быстрый путь' if plan.source == 'fast' else 'модель'}"
                         + (f", {plan.seconds:.2f} с" if plan.source == "llm" else "")]
                for name, args in plan.actions:
                    s = self.app.assistant.by_name.get(name)
                    lines.append(f"→ {s.title if s else name} {args if args else ''}")
                if plan.reply:
                    lines.append(f"Ответ: {plan.reply}")
                if plan.error:
                    lines.append(f"Ошибка: {plan.error}")
                if not plan.actions and not plan.reply and not plan.error:
                    lines.append("Команда не распознана.")

                def show():
                    out.delete("1.0", "end")
                    out.insert("end", "\n".join(lines))
                self.ui.call(show)
            threading.Thread(target=work, daemon=True).start()

        ttk.Button(row, text="Проверить", command=run).pack(side="left", padx=(PAD, 0))
        entry.bind("<Return>", lambda e: run())
        return f

    def save(self):
        wake = self.wake.get().strip().lower()
        if not wake or " " in wake or (self.app.listener and not self.app.listener.is_word(wake)):
            messagebox.showwarning("Рэм", "Слово активации не подходит — см. подсказку рядом с полем.",
                                   parent=self.win)
            return
        c = self.app.config
        c["wake_word"] = wake
        c["model"] = self.model.get().strip()
        c["keep_alive_min"] = max(1, min(30, int(self.keep.get())))
        c["game_mode"] = next(k for k, v in self.GAME_MODES.items() if v == self.game.get())
        c["search_engine"] = self.engine.get()
        c["speak_replies"] = bool(self.speak.get())
        c["disabled_skills"] = [n for n, (on, _, _) in self.skill_vars.items() if not on.get()]
        c["confirm_overrides"] = {n: ask.get() for n, (_, ask, default) in self.skill_vars.items()
                                  if ask.get() != default}
        c["custom_skills"] = self.custom
        cfgmod.save(c)
        try:
            autostart.set_enabled(bool(self.auto.get()))
        except OSError:
            pass
        self.app.apply_config()
        self.win.destroy()
