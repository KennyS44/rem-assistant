"""Оценка задач в окнах «по цели»: модель смотрит на окно и сама выбирает, что нажать.
Окна ненастоящие — сценарии ниже меняют состояние после каждого нажатия. Требует запущенную Ollama.

    python -m tests.eval_click --model qwen3:4b-instruct-2507-q4_K_M [--url http://127.0.0.1:11434]
"""
import argparse
import time

from rem import uia, winapi
from rem.assistant import Assistant
from rem.brain import Ollama
from rem.config import DEFAULTS
from rem.skills import REGISTRY, screen
from rem.skills.apps import AppIndex
from rem.uia import Control as C

B, T, M = "кнопка", "текст", "меню"

# сценарий: команда, окно, состояния {имя: (элементы, {нажатая кнопка: следующее состояние})}, проверка
SCENARIOS = [
    ("закрой блокнот без сохранения", "*Безымянный — Блокнот", "notepad", "editor", {
        "editor": ([C("Файл", M), C("Правка", M), C("Формат", M), C("Вид", M), C("Справка", M)], {}),
        "dialog": ([C("Сохранить изменения в файле «Безымянный»?", T), C("Сохранить", B),
                    C("Не сохранять", B), C("Отмена", B)], {"Не сохранять": "gone", "Сохранить": "saveas"}),
        "saveas": ([C("Имя файла:", T), C("Сохранить", B), C("Отмена", B)], {}),
        "gone": ([], {}),
    }, lambda p, st: p == ["Не сохранять"] and st == "gone"),

    ("пройди установку до конца", "Установка — Видеоплеер", "setup", "welcome", {
        "welcome": ([C("Вас приветствует мастер установки Видеоплеер", T), C("Далее >", B), C("Отмена", B)],
                    {"Далее >": "license"}),
        "license": ([C("Лицензионное соглашение", T), C("Я принимаю условия соглашения", "переключатель"),
                     C("Я не принимаю условия соглашения", "переключатель"), C("< Назад", B),
                     C("Далее >", B, enabled=False), C("Отмена", B)],
                    {"Я принимаю условия соглашения": "license_ok"}),
        "license_ok": ([C("Лицензионное соглашение", T), C("Я принимаю условия соглашения", "переключатель"),
                        C("Я не принимаю условия соглашения", "переключатель"), C("< Назад", B),
                        C("Далее >", B), C("Отмена", B)], {"Далее >": "folder"}),
        "folder": ([C("Выберите папку установки", T), C("C:\\Program Files\\Видеоплеер", T), C("Обзор…", B),
                    C("< Назад", B), C("Далее >", B), C("Отмена", B)], {"Далее >": "ready"}),
        "ready": ([C("Всё готово к установке", T), C("< Назад", B), C("Установить", B), C("Отмена", B)],
                  {"Установить": "finish"}),
        "finish": ([C("Установка Видеоплеер завершена", T), C("Запустить Видеоплеер", "флажок"),
                    C("Завершить", B)], {"Завершить": "gone"}),
        "gone": ([], {}),
    }, lambda p, st: st == "gone" and "Установить" in p),

    ("согласись с условиями и продолжи", "Установка — Видеоплеер", "setup", "license", None,
     lambda p, st: p[:2] == ["Я принимаю условия соглашения", "Далее >"]),

    ("открой вкладку вид", "Документ1 — Word", "winword", "word", {
        "word": ([C("Файл", "вкладка"), C("Главная", "вкладка"), C("Вставка", "вкладка"), C("Макет", "вкладка"),
                  C("Вид", "вкладка"), C("Сохранить", B), C("Отменить", B)], {"Вид": "word"}),
    }, lambda p, st: p == ["Вид"]),

    ("отключи запуск вместе с windows и сохрани настройку", "Настройки — Мессенджер", "messenger", "settings", {
        "settings": ([C("Общие", "вкладка"), C("Уведомления", "вкладка"),
                      C("Запускать вместе с Windows", "флажок"), C("Сворачивать в трей", "флажок"),
                      C("ОК", B), C("Отмена", B), C("Применить", B)],
                     {"Запускать вместе с Windows": "settings", "ОК": "gone", "Применить": "settings"}),
        "gone": ([], {}),
    }, lambda p, st: p and p[0] == "Запускать вместе с Windows" and ("ОК" in p or "Применить" in p)),

    ("удали этот файл", "Удаление файла", "explorer", "delete", {
        "delete": ([C("Вы действительно хотите переместить этот файл в корзину?", T), C("отчёт.docx", T),
                    C("Да", B), C("Нет", B)], {"Да": "gone"}),
        "gone": ([], {}),
    }, lambda p, st: p == ["Да"]),

    ("нажми не сохранять", "Блокнот", "notepad", "dialog", None, lambda p, st: p == ["Не сохранять"]),

    ("что тут можно нажать", "Установка — Видеоплеер", "setup", "welcome", None, lambda p, st: p == []),
]
SCENARIOS = [(t, title, exe, start, states or SCENARIOS[1 if exe == "setup" else 0][4], ok)
             for t, title, exe, start, states, ok in SCENARIOS]


def run_case(a: Assistant, case, monkeypatch) -> tuple[bool, list, str, float, list]:
    text, title, exe, start, states, ok = case
    st = {"now": start}
    pressed, asked = [], []

    def controls(hwnds):
        return states[st["now"]][0]

    def press(hwnds, spoken, ask=None):
        found = controls(hwnds)
        c = uia.best_match(spoken, found)
        if c is None:
            return "missing", ""
        if not c.enabled:
            return "disabled", c.name
        texts = tuple(t.name for t in found if t.kind == T)
        if ask and uia.dangerous(c.name, spoken, texts=texts) and not ask(c.name):
            return "cancelled", c.name
        pressed.append(c.name)
        st["now"] = states[st["now"]][1].get(c.name, st["now"])
        return "pressed", c.name

    def close_app(ctx, name):
        if st["now"] == "editor":
            st["now"] = "dialog"

    win = lambda: {"hwnd": 1, "pid": 1, "title": title if st["now"] != "gone" else "Рабочий стол", "exe": exe + ".exe"}
    monkeypatch(screen, "_target", lambda ctx, window="": (win(), [1]))
    monkeypatch(uia, "controls", controls)
    monkeypatch(uia, "find_and_press", press)
    monkeypatch(REGISTRY["close_app"], "handler", close_app)
    monkeypatch(winapi, "open_windows", lambda: [] if st["now"] == "gone" else [{**win(), "active": True}])
    monkeypatch(a, "_confirm", lambda q: asked.append(q) or True)
    a.recent.clear()
    a.fast.match = lambda t: None                 # «нажми …» тоже через модель — проверяем её
    replies = []
    a.say = replies.append
    t0 = time.perf_counter()
    a.handle(text)
    return bool(ok(pressed, st["now"])), pressed, " ".join(replies), time.perf_counter() - t0, asked


class Patch:
    def __init__(self):
        self.undo = []

    def __call__(self, obj, name, value):
        self.undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, value in reversed(self.undo):
            setattr(obj, name, value)
        self.undo.clear()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--rem-style", action="store_true")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    cfg = {**DEFAULTS, "model": args.model, "rem_style": args.rem_style}
    client = Ollama(args.url, timeout=900)
    tokens = []                                   # сколько токенов ушло в модель (окно — 4096)
    post = client._post
    client._post = lambda path, body, timeout=None: (lambda r: tokens.append(r.get("prompt_eval_count", 0)
                                                                            + r.get("eval_count", 0)) or r)(
        post(path, body, timeout))
    a = Assistant(cfg, client=client, apps=AppIndex([]))
    a.journal = a.journal.with_name("eval_click.log")
    good = total = 0
    for _ in range(args.repeat):
        for case in SCENARIOS:
            p = Patch()
            try:
                ok, pressed, said, sec, asked = run_case(a, case, p)
            finally:
                p.restore()
            good, total = good + ok, total + 1
            used, tokens[:] = max(tokens or [0]), []
            mark = "✓" if ok else "✗"
            print(f"{mark} «{case[0]}» → нажато {pressed}, спросила {asked}, сказала «{said}» ({sec:.1f} с, до {used} токенов)", flush=True)
    print(f"итого {good}/{total}")
