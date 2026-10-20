"""Кнопки, меню, вкладки и поля ввода в окнах других программ — через Windows UI Automation.

Windows сама перечисляет элементы окна с их названиями (так работают экранные дикторы),
поэтому картинку экрана разбирать не нужно: быстро и без видеокарты. Игры и программы,
которые рисуют кнопки сами, элементов не отдают — в них Рэм кнопку не найдёт.

Поиск и нажатие идут в отдельном потоке: нажатие кнопки, которая открывает модальное
окно, у некоторых программ не возвращается, пока это окно не закроют.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from . import winapi
from .text import norm, translit

# тип элемента UI Automation → как назвать его модели
KINDS = {
    50000: "кнопка", 50031: "кнопка", 50011: "меню", 50019: "вкладка", 50005: "ссылка",
    50002: "флажок", 50013: "переключатель", 50007: "пункт списка", 50024: "пункт дерева",
    50003: "выпадающий список", 50020: "текст", 50004: "поле", 50030: "поле",
}
PRIORITY = ["кнопка", "меню", "вкладка", "флажок", "переключатель", "ссылка",
            "выпадающий список", "пункт списка", "пункт дерева", "поле"]
MAX_ELEMENTS = 400

# в английских программах кнопки по-английски, а говорят по-русски
EN = {
    "ок": "ok", "окей": "ok", "да": "yes", "нет": "no", "отмена": "cancel", "отменить": "cancel",
    "далее": "next", "дальше": "next", "назад": "back", "готово": "finish", "закрыть": "close",
    "применить": "apply", "сохранить": "save", "не сохранять": "don t save", "открыть": "open",
    "установить": "install", "принимаю": "i agree", "согласен": "i agree", "пропустить": "skip",
    "продолжить": "continue", "удалить": "delete", "обзор": "browse", "файл": "file",
    "правка": "edit", "вид": "view", "справка": "help", "выход": "exit", "повторить": "retry",
    "разрешить": "allow", "войти": "sign in", "поиск": "search", "настройки": "settings",
}
# «нажми на кнопку сохранить» — нужны только слова с экрана
LEAD = ("на ", "по ", "в ", "кнопку ", "кнопке ", "кнопка ", "пункт ", "пункту ", "меню ", "вкладку ", "вкладке ",
        "ссылку ", "ссылке ", "флажок ", "галочку ", "поле ", "строку ", "строке ")

# такие кнопки Рэм нажимает только после «да»
DANGER = ("удал", "отправ", "оплат", "купи", "покуп", "заказ", "формат", "стере", "очист", "сброс",
          "delete", "remove", "send", "pay", "buy", "purchase", "order", "format", "erase", "reset",
          "uninstall", "publish", "опублик", "корзин")
# согласие в диалоге «Удалить файл?» — тоже опасно
YES = {"да", "yes", "ok", "ок", "продолжить", "continue", "подтвердить", "confirm", "готово"}


@dataclass
class Control:
    name: str
    kind: str
    rect: tuple = (0, 0, 0, 0)          # left, top, right, bottom
    enabled: bool = True
    on: bool | None = None              # флажок отмечен, вариант выбран; None — не бывает
    value: str = ""                     # что написано в поле ввода
    el: object = field(default=None, repr=False)   # элемент UI Automation (только в Windows)


def spoken_label(text: str) -> str:
    q = norm(text)
    changed = True
    while changed:
        changed = False
        for w in LEAD:
            if q.startswith(w) and len(q) > len(w):
                q, changed = q[len(w):], True
    return q


def _score(cand: str, name: str) -> float:
    if cand == name:
        return 100
    if len(cand) <= 3 or len(name) <= 3:           # «да» ≠ «далее»: короткие — только точно
        return 0
    score = fuzz.ratio(cand, name)
    if set(cand.split()) <= set(name.split()):     # «сохранить» ⊂ «сохранить как»
        score = max(score, 88)
    return score


def best_match(spoken: str, controls: list[Control], cutoff: float = 80) -> Control | None:
    """Элемент, который назвал человек: сначала точное название, потом похожее.
    При равенстве — кнопка раньше пункта списка, доступная раньше серой, верхнее окно раньше."""
    q = spoken_label(spoken)
    if not q:
        return None
    cands = list(dict.fromkeys([q, EN.get(q, q), translit(q)]))
    best, best_key = None, None
    for i, c in enumerate(controls):
        if c.kind == "текст":
            continue
        name = norm(c.name)
        score = max(_score(cand, name) for cand in cands)
        if score < cutoff:
            continue
        key = (score, c.enabled, -PRIORITY.index(c.kind) if c.kind in PRIORITY else -99, -i)
        if best_key is None or key > best_key:
            best, best_key = c, key
    return best


def dangerous(*names: str, texts: tuple = ()) -> bool:
    """Опасное нажатие: «Удалить», «Отправить»… или «Да» в окне, где спрашивают про удаление."""
    if any(d in norm(n) for n in names for d in DANGER):
        return True
    return any(norm(n) in YES for n in names) and any(d in norm(t) for t in texts for d in DANGER)


def describe(title: str, exe: str, controls: list[Control], limit: int = 700) -> str:
    """Короткое описание окна для модели: текст и что можно нажать, по видам."""
    exe = exe.removesuffix(".exe").removesuffix(".EXE")
    head = f"Окно «{title[:80]}»" + (f" ({exe})" if exe else "") + ":"
    texts, groups, seen = [], {}, set()
    for c in controls:
        name = " ".join(c.name.split())[:60]
        if c.kind == "поле":
            name = name or "без названия"
        if not name or (c.kind, name) in seen:
            continue
        seen.add((c.kind, name))
        if c.kind == "текст":
            if len(texts) < 4 and name != title:
                texts.append(f"«{name}»")
            continue
        mark = "" if c.enabled else " (неактивна)"
        if c.kind == "флажок" and c.on is not None:
            mark += " (отмечен)" if c.on else " (не отмечен)"
        elif c.on:
            mark += " (выбрано)"
        if c.kind == "поле" and c.value.strip():
            mark += f" = «{' '.join(c.value.split())[:40]}»"
        groups.setdefault(c.kind, []).append((name if name == "без названия" else f"«{name}»") + mark)
    lines = [head]
    if texts:
        lines.append("Текст: " + " ".join(texts))
    for kind in PRIORITY:
        if kind in groups:
            lines.append(f"{kind}: " + ", ".join(groups[kind]))
    if len(lines) == 1:
        lines.append("кнопок не видно (программа рисует их сама или окно пустое)")
    out = "\n".join(lines)
    return out if len(out) <= limit else out[:limit].rsplit(",", 1)[0] + " …"


# ——— Windows ———

def _in_thread(fn, timeout: float):
    """Выполнить fn в своём потоке COM (MTA). Не дождались — None (поток доработает сам)."""
    box: queue.Queue = queue.Queue()

    def run():
        import comtypes
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass
        try:
            box.put((True, fn(_automation())))
        except Exception as e:
            box.put((False, e))

    threading.Thread(target=run, daemon=True).start()
    try:
        ok, value = box.get(timeout=timeout)
    except queue.Empty:
        return None
    if not ok:
        raise value
    return value


def _module():
    import comtypes.client
    comtypes.client.GetModule("UIAutomationCore.dll")        # в сборке — уже готовый comtypes.gen
    from comtypes.gen import UIAutomationClient
    return UIAutomationClient


def _automation():
    import comtypes.client
    m = _module()
    return comtypes.client.CreateObject(m.CUIAutomation._reg_clsid_, interface=m.IUIAutomation)


def _controls(uia, hwnds: list[int]) -> list[Control]:
    m = _module()
    cache = uia.CreateCacheRequest()
    for prop in (30005, 30003, 30001, 30010, 30022, 30086, 30079, 30045):
        cache.AddProperty(prop)         # имя, тип, рамка, доступен, за экраном, флажок, выбран, текст поля
    cond = None
    for kind in KINDS:
        c = uia.CreatePropertyCondition(30003, kind)
        cond = c if cond is None else uia.CreateOrCondition(cond, c)
    out: list[Control] = []
    for hwnd in hwnds:
        try:
            root = uia.ElementFromHandle(hwnd)
            found = root.FindAllBuildCache(m.TreeScope_Descendants, cond, cache)
        except Exception:
            continue                                   # окно закрылось или не отвечает
        for i in range(min(found.Length, MAX_ELEMENTS - len(out))):
            e = found.GetElement(i)
            if e.CachedIsOffscreen:
                continue
            r = e.CachedBoundingRectangle
            kind = KINDS.get(e.CachedControlType, "текст")
            out.append(Control(e.CachedName or "", kind, (r.left, r.top, r.right, r.bottom),
                               bool(e.CachedIsEnabled), _state(e), _value(e) if kind == "поле" else "", e))
        if len(out) >= MAX_ELEMENTS:
            break
    return out


def _state(e) -> bool | None:
    """Флажок отмечен (ToggleState = 1) или вариант выбран (IsSelected); у кнопок — None."""
    for prop in (30086, 30079):
        try:
            v = e.GetCachedPropertyValue(prop)
        except Exception:
            continue
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return v == 1
    return None


def _value(e) -> str:
    try:
        v = e.GetCachedPropertyValue(30045)            # ValueValue
    except Exception:
        return ""
    return v if isinstance(v, str) else ""


def controls(hwnds: list[int], timeout: float = 8) -> list[Control]:
    """Видимые кнопки, меню, вкладки, флажки, ссылки и тексты окон hwnds (по порядку)."""
    winapi._need_windows()
    found = _in_thread(lambda uia: _controls(uia, hwnds), timeout)
    if found is None:
        raise RuntimeError("окно не ответило вовремя")
    return found


def _press(c: Control) -> None:
    m = _module()
    patterns = ((10000, m.IUIAutomationInvokePattern, "Invoke"),
                (10015, m.IUIAutomationTogglePattern, "Toggle"),
                (10010, m.IUIAutomationSelectionItemPattern, "Select"),
                (10005, m.IUIAutomationExpandCollapsePattern, "Expand"))
    for pid, iface, method in patterns:
        try:
            p = c.el.GetCurrentPattern(pid)
            if p:
                getattr(p.QueryInterface(iface), method)()
                return
        except Exception:
            continue
    winapi.click_at((c.rect[0] + c.rect[2]) // 2, (c.rect[1] + c.rect[3]) // 2)   # как мышью


def find_and_press(hwnds: list[int], spoken: str, ask=None, timeout: float = 8) -> tuple[str, str]:
    """Найти элемент по названию и нажать. ask(название) → bool — для опасных кнопок.
    → (что вышло: pressed | missing | disabled | cancelled, название на экране)."""
    winapi._need_windows()
    result: dict = {}

    def work(uia):
        found = _controls(uia, hwnds)
        c = best_match(spoken, found)
        if c is None:
            return "missing", ""
        if not c.enabled:
            return "disabled", c.name
        texts = tuple(t.name for t in found if t.kind == "текст")
        if ask and dangerous(c.name, spoken, texts=texts) and not ask(c.name):
            return "cancelled", c.name
        result["name"] = c.name
        _press(c)
        return "pressed", c.name

    out = _in_thread(work, timeout + 60 * bool(ask))   # подтверждение голосом — дольше
    if out is None:
        if "name" in result:              # нажатие открыло модальное окно и ещё не вернулось
            return "pressed", result["name"]
        raise RuntimeError("окно не ответило вовремя")
    time.sleep(0.4)                       # окно успевает перерисоваться
    return out


def pick_field(spoken: str, found: list[Control]) -> Control | None | str:
    """Поле для текста: названное, иначе единственное в окне. "caret" — полей не видно совсем
    (программа их не показывает) или их несколько, а название не сказано: пишем туда, где курсор."""
    fields = [c for c in found if c.kind == "поле" and c.enabled]
    if spoken:
        hit = best_match(spoken, fields)
        if hit:
            return hit
    if len(fields) == 1:
        return fields[0]
    if not fields or not spoken:
        return "caret"
    return None


def focus_field(hwnds: list[int], spoken: str = "", timeout: float = 8) -> tuple[str, str]:
    """Поставить курсор в поле. → (focused | caret | missing, название поля)."""
    winapi._need_windows()

    def work(uia):
        c = pick_field(spoken, _controls(uia, hwnds))
        if c is None:
            return "missing", ""
        if c == "caret":
            return "caret", ""
        try:
            c.el.SetFocus()
        except Exception:
            winapi.click_at((c.rect[0] + c.rect[2]) // 2, (c.rect[1] + c.rect[3]) // 2)
        return "focused", c.name

    out = _in_thread(work, timeout)
    if out is None:
        raise RuntimeError("окно не ответило вовремя")
    time.sleep(0.15)
    return out
