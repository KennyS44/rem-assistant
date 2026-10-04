"""Мелочи для русского текста: склонение по числу, нормализация, транслитерация."""
import re


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def norm(s: str) -> str:
    """Нижний регистр, ё→е, без пунктуации и лишних пробелов."""
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s%+-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_TR = dict(zip("абвгдезийклмнопрстуфхцы", "abvgdeziyklmnoprstufhcy"))
_TR.update({"ж": "zh", "ч": "ch", "ш": "sh", "щ": "sch", "ю": "yu", "я": "ya", "э": "e",
            "ё": "e", "ъ": "", "ь": ""})


def translit(s: str) -> str:
    """Грубая транслитерация: «дискорд» → «diskord» — для поиска программ по имени."""
    return "".join(_TR.get(c, c) for c in s.lower())


def duration_ru(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    parts = []
    if h:
        parts.append(f"{h} {plural(h, 'час', 'часа', 'часов')}")
    if m:
        parts.append(f"{m} {plural(m, 'минуту', 'минуты', 'минут')}")
    if s or not parts:
        parts.append(f"{s} {plural(s, 'секунду', 'секунды', 'секунд')}")
    return " ".join(parts)
