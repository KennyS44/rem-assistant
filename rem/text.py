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


def elapsed_ru(seconds: int) -> str:
    """«5 минут прошли», «1 минута прошла», «1 час прошёл» — для фразы о конце таймера.
    Число в начале, глагол в конце: так смысловое ударение падает на «прошли», а не на число."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    parts = []
    if h:
        parts.append((h, plural(h, "час", "часа", "часов"), "прошёл"))
    if m:
        parts.append((m, plural(m, "минута", "минуты", "минут"), "прошла"))
    if s or not parts:
        parts.append((s, plural(s, "секунда", "секунды", "секунд"), "прошла"))
    n, _, one_verb = parts[-1]
    verb = one_verb if n % 10 == 1 and n % 100 != 11 else "прошли"
    return " ".join(f"{n} {w}" for n, w, _ in parts) + " " + verb


_ONES = "ноль один два три четыре пять шесть семь восемь девять".split()
_TEENS = ("десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать "
          "семнадцать восемнадцать девятнадцать").split()
_TENS = "_ _ двадцать тридцать сорок пятьдесят шестьдесят семьдесят восемьдесят девяносто".split()
_HUNDREDS = "_ сто двести триста четыреста пятьсот шестьсот семьсот восемьсот девятьсот".split()
_SCALES = [(10**9, ("миллиард", "миллиарда", "миллиардов"), False),
           (10**6, ("миллион", "миллиона", "миллионов"), False),
           (1000, ("тысяча", "тысячи", "тысяч"), True)]
_ORD = ("первое второе третье четвёртое пятое шестое седьмое восьмое девятое десятое одиннадцатое "
        "двенадцатое тринадцатое четырнадцатое пятнадцатое шестнадцатое семнадцатое восемнадцатое "
        "девятнадцатое двадцатое").split()
_MONTHS_GEN = ("января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря")
_FEM = re.compile(r"(минут|секунд|тысяч|недел|сут|копе|штук|страниц|вкладк|папк|программ|ошибк|задач)")


def _triple(n: int, fem: bool) -> list[str]:
    w = []
    if n >= 100:
        w.append(_HUNDREDS[n // 100])
    n %= 100
    if 10 <= n < 20:
        w.append(_TEENS[n - 10])
        return w
    if n >= 20:
        w.append(_TENS[n // 10])
    n %= 10
    if n:
        w.append({1: "одна", 2: "две"}[n] if fem and n in (1, 2) else _ONES[n])
    return w


def number_words(n: int, fem: bool = False) -> str:
    """42 → «сорок два»; fem — «одна/две» (минута, секунда)."""
    if n == 0:
        return "ноль"
    if n < 0:
        return "минус " + number_words(-n, fem)
    w = []
    for size, forms, f in _SCALES:
        if n >= size:
            k, n = divmod(n, size)
            w += _triple(k, f) if k < 1000 else number_words(k).split()
            w.append(plural(k, *forms))
    return " ".join(w + _triple(n, fem))


def _ordinal(n: int) -> str:
    if n <= 20:
        return _ORD[n - 1]
    tens = {2: "двадцать", 3: "тридцать"}[n // 10]
    return tens + ("ое" if n % 10 == 0 else " " + _ORD[n % 10 - 1])


def say_numbers(text: str) -> str:
    """Цифры словами — нейроголос Silero цифры просто пропускает.
    «5 октября» → «пятое октября», «8:15» → «восемь пятнадцать», «35%» → «тридцать пять процентов»,
    «на 1 минуту» → «на одну минуту»."""
    text = re.sub(rf"\b([1-9]|[12]\d|3[01]) (?={_MONTHS_GEN})", lambda m: _ordinal(int(m[1])) + " ", text)
    text = re.sub(r"\b(\d{1,2}):(\d\d)\b", lambda m: f"{int(m[1])} " + ("ноль " if m[2][0] == "0" and m[2] != "00" else "")
                  + ("ровно" if m[2] == "00" else str(int(m[2]))), text)
    text = re.sub(r"(\d+)\s?%", lambda m: f"{m[1]} {plural(int(m[1]), 'процент', 'процента', 'процентов')}", text)
    text = re.sub(r"\b(\d+)[.,](\d+)\b", lambda m: f"{m[1]} и {m[2]}", text)

    def one(m):
        n, after = int(m[1].replace(" ", "")), text[m.end():m.end() + 12].lstrip().lower()
        neg = m[0].startswith("-")
        words = number_words(n, bool(_FEM.match(after)))
        if after.startswith(("минуту", "секунду", "неделю")):
            words = re.sub(r"одна$", "одну", words)
        return ("минус " if neg else "") + words

    return re.sub(r"(?<![\w-])-?(\d{1,3}(?: \d{3})+|\d+)(?!\w)", one, text)
