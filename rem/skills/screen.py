"""Кнопки и поля в открытых окнах: «нажми Сохранить», «напиши в поиск…», «что в окне»,
вкладки («следующая вкладка», «вкладка почта») и чаты Telegram («открой чат с мамой»).

Элементы окна берутся из Windows UI Automation (см. rem/uia.py). Опасные кнопки
(«Удалить», «Отправить», «Оплатить»…) Рэм нажимает только после голосового «да».
Когда нажатие выбрала модель, она видит окно после нажатия и решает, что дальше, —
так проходятся задачи в несколько нажатий («закрой блокнот без сохранения»).
"""
from __future__ import annotations

import re
import time

from rapidfuzz import fuzz

from .. import uia, winapi
from ..text import norm, translit
from .apps import find_windows, open_app
from .web import SITES
from .base import Cancelled, NotHere, Param, skill


def _target(ctx, window: str = "") -> tuple[dict, list[int]]:
    """Окно, о котором речь (по названию или активное), и все видимые окна той же программы —
    в них бывают диалоги и раскрытые меню. Своё окно первым."""
    if window:
        hits = find_windows(ctx, window)
        if not hits:
            raise LookupError(f"Окно «{window}» не найдено.")
        w = hits[0]
    else:
        w = winapi.foreground_window()
        if not w:
            raise LookupError("Нет открытых окон.")
    others = [h for h in winapi.windows_of_pids({w["pid"]}) if h != w["hwnd"]]
    return w, [w["hwnd"]] + others[:5]


def look(ctx, window: str = "") -> str:
    try:
        w, hwnds = _target(ctx, window)
    except LookupError as e:
        return str(e)
    return uia.describe(w["title"], w["exe"], uia.controls(hwnds))


def active_dialog(ctx, max_buttons: int = 12) -> str:
    """Кнопки активного окна, если это небольшое окно (диалог, мастер установки): модель сразу
    видит, что значит «подтверди» или «согласись». Большие окна (браузер, Word) — только по look_at_window."""
    w = winapi.foreground_window()
    if not w:
        return ""
    found = uia.controls([w["hwnd"]], timeout=1.5)
    if not found or sum(c.kind != "текст" for c in found) > max_buttons:
        return ""
    return "Активное о" + uia.describe(w["title"], w["exe"], found, limit=400)[1:]    # «Окно…» → «Активное окно…»


def after_click(ctx, name: str = "", window: str = "") -> str:
    """Что стало с окном после нажатия — модель решит, нужно ли нажать что-то ещё."""
    return "Теперь " + look(ctx, window)


@skill("click_button", "Нажать кнопку в окне",
       "Нажать в окне кнопку, пункт меню, вкладку или флажок по названию на экране. "
       "window — только если нужно другое, не активное окно.",
       params=[Param("name", "string", "название на экране"),
               Param("window", "string", "программа или заголовок окна", required=False)],
       examples=["нажми {label}", "нажми на {label}", "нажми кнопку {label}", "нажми на кнопку {label}",
                 "кликни {label}", "кликни на {label}", "щелкни {label}", "щелкни на {label}",
                 "жми {label}"],
       category="Окна", observe=after_click)
def click_button(ctx, name: str, window: str = ""):
    try:
        _, hwnds = _target(ctx, window)
    except LookupError as e:
        raise NotHere(str(e))
    what, label = uia.find_and_press(hwnds, name, ask=lambda n: ctx.confirm(f"Нажать «{n}»"))
    if what == "missing":
        raise NotHere(f"Кнопки «{name}» в окне не видно.")
    if what == "disabled":
        return f"Кнопка «{label}» сейчас неактивна."
    if what == "cancelled":
        raise Cancelled
    return None


@skill("look_at_window", "Что в окне",
       "Посмотреть текст и кнопки окна — перед нажатиями, когда задача в окне в несколько шагов.",
       params=[Param("window", "string", "программа или заголовок окна", required=False)],
       examples=["что в окне", "что можно нажать", "какие тут кнопки", "какие есть кнопки",
                 "что на экране", "прочитай окно"],
       category="Окна", retell=True)
def look_at_window(ctx, window: str = ""):
    return look(ctx, window)


# здесь Enter отправляет сообщение людям — сначала спросить
MESSENGERS = ("telegram", "discord", "whatsapp", "viber", "skype", "slack", "teams", "zoom", "outlook",
              "thunderbird", "signal")


def _sends_to_people(w: dict, field: str) -> bool:
    exe = w.get("exe", "").lower()
    where = f"{exe} {w.get('title', '')} {field}".lower()
    return any(m in exe for m in MESSENGERS) or any(s in where for s in ("сообщ", "message", "чат", "chat",
                                                                         "почт", "mail"))


@skill("type_text", "Вписать текст в окно",
       "Вписать текст в поле окна: сообщение, поиск, адрес, имя файла. field — название поля, если полей "
       "несколько; без него — в единственное поле или туда, где курсор. enter=да — после текста нажать "
       "Enter (отправить, найти).",
       params=[Param("text", "string", "что вписать, как сказал пользователь"),
               Param("field", "string", "название поля", required=False),
               Param("enter", "string", "нажать Enter после текста", enum=["да", "нет"], required=False),
               Param("window", "string", "программа или заголовок окна", required=False)],
       category="Окна")
def type_text(ctx, text: str, field: str = "", enter: str = "нет", window: str = ""):
    try:
        w, hwnds = _target(ctx, window)
    except LookupError as e:
        raise NotHere(str(e))
    if not winapi.is_foreground(w["hwnd"]):
        winapi.focus_window(w["hwnd"])          # клавиатура печатает только в активное окно
        time.sleep(0.3)                         # Alt из focus_window ушёл старому окну — даём переключиться
    what, label = uia.focus_field(hwnds, field)
    if what == "missing":
        raise NotHere(f"Поля «{field}» в окне не видно.")
    winapi.type_unicode(text)
    if enter == "да":
        if _sends_to_people(w, label or field) and not ctx.confirm("Отправить"):
            raise Cancelled                       # текст остаётся в поле, не отправлен
        winapi.press(winapi.VK["enter"])
    return None


# ——— названия, сказанные в падеже: «с мамой» → чат «Мама», «вкладку почту» → «Почта» ———

_ENDINGS = ("ами", "ями", "ою", "ею", "ой", "ей", "ом", "ем", "ам", "ям", "ах", "ях",
            "у", "ю", "е", "а", "я", "ы", "и", "о")


def _stem(word: str) -> str:
    for e in _ENDINGS:
        if word.endswith(e) and len(word) - len(e) >= 3:
            return word[:-len(e)]
    return word


def _stems(text: str) -> list[str]:
    return [_stem(w) for w in norm(text).split() if w not in ("с", "со", "в", "во", "на")]


# «вкладку с ютубом» → заголовок «YouTube»
_SITE_WORDS = {_stem(k): v.split("/")[0].split(".")[-2] for k, v in SITES.items()}


def name_score(spoken: str, name: str) -> float:
    """Насколько сказанное похоже на название: «мамой» ~ «Мама 🌸», «почту» ~ «Почта - Memory usage»."""
    q, t = _stems(spoken), _stems(name)
    if not q or not t:
        return 0
    site = [_SITE_WORDS.get(w, w) for w in q]
    if site != q:
        return max(name_score(" ".join(site), name), _score(q, t))
    return _score(q, t)


def _score(q: list[str], t: list[str]) -> float:
    if q == t:
        return 100
    if set(q) <= set(t):
        return 90
    a, b = " ".join(q), " ".join(t)
    return max(fuzz.ratio(a, b), fuzz.ratio(translit(a), translit(b)))


def find_named(spoken: str, names: list[str], cutoff: float = 85) -> int | None:
    """Индекс лучшего названия или None."""
    best, best_score = None, cutoff - 0.01
    for i, n in enumerate(names):
        score = name_score(spoken, n)
        if score > best_score:
            best, best_score = i, score
    return best


# ——— вкладки ———

_ORDINAL = re.compile(r"^(перв|втор|трет|четверт|пят|шест|седьм|восьм)(ая|ую|ой|ья|ью|ей|ий|ый|ое)$")
_ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8}


def tab_keys(spoken: str) -> tuple[int, ...] | None:
    """Сочетание клавиш для «следующая», «предыдущая», «последняя», «третья», «3»; иначе None (это название)."""
    vk = winapi.VK
    q = " ".join(w for w in norm(spoken).split() if w not in ("вкладка", "вкладку", "вкладке", "на", "в", "во"))
    if q.startswith(("следующ", "дальше", "вперед")):
        return vk["ctrl"], vk["tab"]
    if q.startswith(("предыдущ", "прошл", "назад")):
        return vk["ctrl"], vk["shift"], vk["tab"]
    if q.startswith("последн"):
        return vk["ctrl"], 0x39
    m = _ORDINAL.match(q)
    n = _ORDINALS[m.group(1)] if m else int(q) if q.isdigit() else 0
    if 1 <= n <= 8:
        return vk["ctrl"], 0x30 + n
    return None


def _title() -> str:
    w = winapi.foreground_window()
    return w["title"] if w else ""


@skill("switch_tab", "Переключить вкладку",
       "Переключить вкладку активного окна: по названию, номеру, следующую или предыдущую.",
       params=[Param("name", "string", "название, номер или «следующая», «предыдущая»")],
       examples=["переключись на вкладку {label}", "переключи на вкладку {label}", "перейди на вкладку {label}",
                 "перейди во вкладку {label}", "открой вкладку {label}", "вкладка {label}",
                 *[(f"{verb}{word}", {"name": word}) for word in (
                     "следующая вкладка", "следующую вкладку", "предыдущая вкладка", "предыдущую вкладку",
                     "прошлая вкладка", "прошлую вкладку", "последняя вкладка", "последнюю вкладку",
                     "первая вкладка", "первую вкладку", "вторая вкладка", "вторую вкладку",
                     "третья вкладка", "третью вкладку", "четвертая вкладка", "четвертую вкладку",
                     "пятая вкладка", "пятую вкладку")
                   for verb in ("", "переключись на ", "перейди на ", "открой ")],
                 ("переключи вкладку", {"name": "следующая"}), ("вкладку вперед", {"name": "следующая"}),
                 ("вкладку назад", {"name": "предыдущая"})],
       category="Окна")
def switch_tab(ctx, name: str):
    w = winapi.foreground_window()
    if not w:
        return "Нет открытых окон."
    keys = tab_keys(name)
    if keys:
        winapi.press(*keys)
        time.sleep(0.4)
        return None if _title() != w["title"] else "Вкладка не переключилась."
    q = uia.spoken_label(name)
    tabs = [c for c in uia.controls([w["hwnd"]]) if c.kind == "вкладка"]
    i = find_named(q, [c.name for c in tabs])
    if i is not None:
        uia.press_control([w["hwnd"]], tabs[i].name, "вкладка")
        return None
    if tabs:
        return f"Вкладки «{q}» не видно."
    # вкладки окно не показывает (Firefox) — листаем Ctrl+Tab и смотрим заголовок окна
    start = w["title"]
    for _ in range(30):
        winapi.press(winapi.VK["ctrl"], winapi.VK["tab"])
        time.sleep(0.3)
        now = _title()
        if find_named(q, [now]) is not None:
            return None
        if now == start:
            break
    return f"Вкладки «{q}» не нашла."


# ——— чаты Telegram ———
# Telegram Desktop показывает список чатов экранным дикторам: каждый чат — пункт списка
# «Имя, …последнее сообщение…». Ищем чат там; не видно — вписываем имя в поиск Telegram.

_CHAT_LEAD = re.compile(r"^(?:(?:чат|переписк\w*|диалог\w*|с|со)\s+)+")
_CHAT_TAIL = re.compile(r"\s+(?:в|во)\s+(?:телеграм\w*|телег\w*|тг|telegram)$|\s+(?:телеграм\w*|telegram)$")


def chat_name(spoken: str) -> str:
    """«с мамой в телеграме» → «мамой»."""
    q = _CHAT_TAIL.sub("", norm(spoken))
    return _CHAT_LEAD.sub("", q).strip()


def chat_title(row: str) -> str:
    """«Мама, получено, Привет, в 12:30» → «Мама»."""
    return row.split(",")[0].strip()


def _chats(found: list) -> list:
    return [c for c in found if c.kind == "пункт списка" and c.name]


def _telegram(ctx) -> dict | None:
    """Окно Telegram на переднем плане; не запущен — запустить и дождаться окна."""
    def find():
        return next((w for w in find_windows(ctx, "telegram") if "telegram" in w["exe"].lower()), None)
    w = find()
    if w is None:
        if open_app(ctx, "Telegram"):
            return None                                   # Telegram не установлен
        deadline = time.monotonic() + 15
        while w is None and time.monotonic() < deadline:
            time.sleep(0.5)
            w = find()
        time.sleep(1)                                     # список чатов успевает загрузиться
    if w and not winapi.is_foreground(w["hwnd"]):
        winapi.focus_window(w["hwnd"])
        time.sleep(0.3)
    return w


@skill("open_chat", "Открыть чат в Telegram",
       "Открыть чат в Telegram по имени человека или группы.",
       params=[Param("name", "string", "имя чата")],
       examples=["открой чат {label}", "открой переписку {label}", "открой диалог {label}", "перейди в чат {label}",
                 "зайди в чат {label}", "открой в телеграме чат {label}", "открой в телеграме {label}",
                 "открой телеграм {label}", "открой телегу {label}"],
       category="Окна")
def open_chat(ctx, name: str):
    who = chat_name(name)
    if not who:
        return "Не расслышала, какой чат открыть."
    w = _telegram(ctx)
    if w is None:
        return "Telegram не найден."
    hwnds = [w["hwnd"]]
    found = uia.controls(hwnds)
    rows = _chats(found)
    i = find_named(who, [chat_title(c.name) for c in rows])
    if i is None:
        fields = [c for c in found if c.kind == "поле"]
        search = uia.best_match("поиск", fields)
        if search is None:
            if not rows:
                return "Telegram не показывает список чатов. Обновите Telegram до последней версии."
            return f"Чата «{who}» в списке не видно."
        uia.focus_field(hwnds, search.name)
        winapi.press(winapi.VK["ctrl"], winapi.VK["a"])  # старый текст поиска заменится
        winapi.type_unicode(" ".join(_stems(who)))         # «мамой» → «мам»: Telegram ищет по началу слов
        deadline = time.monotonic() + 4
        while i is None and time.monotonic() < deadline:
            time.sleep(0.4)
            rows = _chats(uia.controls(hwnds))
            i = find_named(who, [chat_title(c.name) for c in rows])
        if i is None:
            return f"Чат «{who}» в Telegram не нашла."
    if not uia.press_control(hwnds, rows[i].name, "пункт списка"):
        return f"Чат «{chat_title(rows[i].name)}» не открылся."
    return None
