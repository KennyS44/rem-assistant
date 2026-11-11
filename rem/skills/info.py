"""Быстрые ответы голосом: погода, калькулятор, курс валют. Только быстрым путём (разбор фразы):
модель этих умений не видит, а считает маленькая модель ненадёжно — здесь счёт точный.

Погода — open-meteo.com, курс — ЦБ РФ (cbr-xml-daily.ru): бесплатно, без ключей и регистрации."""
from __future__ import annotations

import ast
import json
import logging
import math
import operator
import re
import time
import urllib.request
from urllib.parse import quote

from ..text import plural
from .base import Param, skill

log = logging.getLogger("rem.info")
UA = {"User-Agent": "Rem voice assistant"}


def _get(url: str, timeout: float = 8.0) -> dict:
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())


def _key(text: str) -> str:
    t = text.lower().replace("ё", "е")
    t = re.sub(r"(?<=\d)[,.](?=\d)", "#", t)              # 2,5 → 2#5: запятая в числе не знак препинания
    t = re.sub(r"(?<=\d)\s(?=\d{3}\b)", "", t)             # «2 300» → «2300»
    t = re.sub(r"[^\w\s%+*/×÷#-]", " ", t).replace("#", ".")
    return " ".join(t.split())


def num(x: float) -> str:
    """345 → «345», 2.5 → «2,5», −3 → «минус 3»: так их читает голос."""
    x = round(x, 2)
    s = (str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").replace(".", ","))
    return s.replace("-", "минус ") if x < 0 else s


# ——— погода ———

WMO = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "туман",
       51: "морось", 53: "морось", 55: "морось", 56: "ледяная морось", 57: "ледяная морось",
       61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь",
       71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
       80: "ливень", 81: "ливень", 82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад",
       95: "гроза", 96: "гроза с градом", 99: "гроза с градом"}
WET = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
CITY_ALIASES = {"питер": "Санкт-Петербург", "питере": "Санкт-Петербург", "спб": "Санкт-Петербург",
                "мск": "Москва", "екб": "Екатеринбург", "нск": "Новосибирск"}
_WEATHER = re.compile(r"\b(погод\w*|зонт\w*|сколько градусов|температур\w* на улице|холодно ли|тепло ли|"
                      r"что надеть)\b")
_CITY = re.compile(r"\b(?:в|во)\s+(?!городе\b)([а-яa-z-]{3,}(?:\s+[а-яa-z-]{3,})?)")
_HOME = re.compile(r"^(?:запомни\s+)?(?:мой город|я живу в|я живу во|я из|город для погоды)\s+(?P<city>.+)$")
NOT_CITY = {"сегодня", "завтра", "выходные", "субботу", "воскресенье", "понедельник", "вторник", "среду",
            "четверг", "пятницу", "улице", "городе", "доме", "комнате", "данный", "этот", "течение"}


def _parse_weather(text: str):
    t = _key(text)
    m = _HOME.match(t)
    if m:
        return {"city": m.group("city"), "day": "home"}
    if not _WEATHER.search(t) or t.startswith(("открой", "зайди", "покажи сайт")):
        return None                                      # «открой погоду» — сайт
    city = ""
    for c in _CITY.findall(t):
        first = c.split()[0]
        if first not in NOT_CITY:
            city = c if c.split()[-1] not in NOT_CITY else first
            break
    day = "tomorrow" if "завтра" in t.split() else "today"
    return {"city": city, "day": "umbrella" if "зонт" in t else day}


def city_candidates(city: str) -> list[str]:
    """«Казани» → «Казани», «Казаня», «Казань»… — именительный падеж ищем перебором окончаний."""
    c = city.strip().lower()
    if c in CITY_ALIASES:
        return [CITY_ALIASES[c]]
    out = [c]
    for end, repl in (("е", "а"), ("е", ""), ("и", "ь"), ("и", "а"), ("е", "я"), ("и", "я"), ("у", "а")):
        if c.endswith(end):
            out.append(c[: -len(end)] + repl)
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def geocode(city: str) -> dict | None:
    """→ {"name", "latitude", "longitude"} или None."""
    for cand in city_candidates(city):
        try:
            found = _get(f"https://geocoding-api.open-meteo.com/v1/search?name={quote(cand)}&count=5&language=ru")
        except Exception as e:
            log.warning("погода: город не найден из-за ошибки: %s", e)
            return None
        want = cand.lower().replace("ё", "е")
        same = [r for r in found.get("results", []) if r["name"].lower().replace("ё", "е") == want]
        if same:
            return max(same, key=lambda r: r.get("population") or 0)
    return None


def degrees(t: float) -> str:
    n = round(t)
    return f"{num(n)} {plural(n, 'градус', 'градуса', 'градусов')}"


def degrees_upto(t: float) -> str:
    """После «до»: «до 1 градуса», «до 3 градусов»."""
    n = round(t)
    return f"{num(n)} {'градуса' if abs(n) % 10 == 1 and abs(n) % 100 != 11 else 'градусов'}"


def weather_text(data: dict, day: str, place: str = "") -> str:
    cur, d = data["current"], data["daily"]
    i = 1 if day == "tomorrow" else 0
    lo, hi = round(d["temperature_2m_min"][i]), round(d["temperature_2m_max"][i])
    rain = d.get("precipitation_probability_max", [None, None])[i]
    sky = WMO.get(d["weather_code"][i], "")
    span = f"от {num(lo)} до {degrees_upto(hi)}" if lo != hi else f"около {degrees_upto(hi)}"
    rain_s = f", вероятность осадков {rain} {plural(rain, 'процент', 'процента', 'процентов')}" if rain is not None else ""
    head = f"{place}, " if place else ""
    if day == "umbrella":
        wet = (rain or 0) >= 50 or d["weather_code"][i] in WET
        verdict = "Зонт лучше взять" if wet else "Зонт, скорее всего, не нужен"
        out = f"{verdict}: {sky}{rain_s}." if sky else f"{verdict}{rain_s}."
        return head + (out[0].lower() + out[1:] if head else out)
    if day == "tomorrow":
        return f"{head}{'з' if head else 'З'}автра {span}, {sky}{rain_s}."
    t, feels = round(cur["temperature_2m"]), round(cur["apparent_temperature"])
    wind = round(cur["wind_speed_10m"])
    now = f"{'с' if head else 'С'}ейчас {degrees(t)}"
    if feels != t:
        now += f", ощущается как {num(feels)}"
    now += f", {WMO.get(cur['weather_code'], sky)}"
    if wind:
        now += f", ветер {wind} {plural(wind, 'метр', 'метра', 'метров')} в секунду"
    return f"{head}{now}. Сегодня {span}{rain_s}."


@skill("weather", "Погода", "Погода сейчас, на сегодня и на завтра; нужен ли зонт.",
       params=[Param("city", "string", "город; пусто — свой", required=False),
               Param("day", "string", "когда", enum=["today", "tomorrow", "umbrella", "home"], required=False)],
       category="Информация", model=False, parse=_parse_weather)
def weather(ctx, city: str = "", day: str = "today"):
    home = ctx.config.get("weather_city", "")
    if day == "home":
        place = geocode(city)
        if place is None:
            return f"Город «{city}» не нашёлся. Скажите полное название."
        ctx.config["weather_city"] = place["name"]
        _save(ctx)
        return f"Хорошо, погоду буду говорить для города {place['name']}."
    if not city and not home:
        return "В каком городе? Скажите, например: «погода в Москве» — первый город Рэм запомнит."
    place = geocode(city or home)
    if place is None:
        return f"Город «{city or home}» не нашёлся."
    if city and not home:                           # первый названный город — свой
        ctx.config["weather_city"] = place["name"]
        _save(ctx)
    try:
        data = _get(f"https://api.open-meteo.com/v1/forecast?latitude={place['latitude']}"
                    f"&longitude={place['longitude']}&current=temperature_2m,apparent_temperature,weather_code,"
                    f"wind_speed_10m&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                    f"weather_code&timezone=auto&forecast_days=2&wind_speed_unit=ms")
    except Exception as e:
        log.warning("погода: %s", e)
        return "Сервис погоды не ответил — нет связи с интернетом?"
    shown = place["name"] if city and place["name"] != home else ""
    return weather_text(data, day, shown)


def _save(ctx) -> None:
    try:
        from .. import config as cfgmod
        cfgmod.save(ctx.config)
    except Exception as e:
        log.warning("город не сохранился: %s", e)


# ——— калькулятор ———

_CALC_START = re.compile(r"^(?:сколько будет|сколько|посчитай|вычисли|подсчитай|реши|рассчитай)\b\s*")
_OPS = [  # фраза → оператор в выражении (длинные раньше коротких)
    (r"процент\w* от", "%of"), (r"% от", "%of"), (r"разделить на|поделить на|делить на|деленное на|поделенное на", "/"),
    (r"умножить на|умножь на|умноженное на|помножить на|умножить", "*"), (r"плюс|прибавить|\+", "+"),
    (r"минус|отнять|вычесть|-", "-"), (r"в квадрате", "**2"), (r"в кубе", "**3"), (r"корень из", "sqrt"),
    (r"[x×*]", "*"), (r"[/÷]", "/"),
]
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 10:
            raise ValueError("степень")
        return _BIN[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand)
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "sqrt" and len(node.args) == 1:
        return math.sqrt(_eval(node.args[0]))
    raise ValueError("не арифметика")


def to_expr(text: str) -> str | None:
    """«сколько будет 15 процентов от 2300» → «15/100*2300». Только числа и действия, иначе None."""
    from ..fastpath import words_to_digits
    t = words_to_digits(_key(text))
    m = _CALC_START.match(t)
    if not m:
        return None
    t = t[m.end():]
    for rx, op in _OPS:
        t = re.sub(rf"(?<![\w.]){rx}(?![\w])" if rx[0].isalpha() else rx, f" {op} ", t)
    t = t.replace("%", " %of ") if "%of" not in t else t
    toks = t.split()
    if not toks or not any(re.fullmatch(r"\d+(?:\.\d+)?", x) for x in toks):
        return None
    out = []
    for i, x in enumerate(toks):
        if re.fullmatch(r"\d+(?:\.\d+)?", x) or x in ("+", "-", "*", "/", "**2", "**3"):
            out.append(x)
        elif x == "%of":
            out.append("/100*")
        elif x == "sqrt":
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if not re.fullmatch(r"\d+(?:\.\d+)?", nxt):
                return None
            out.append("sqrt(")
            toks[i + 1] = nxt + ")"
        elif re.fullmatch(r"\d+(?:\.\d+)?\)", x):
            out.append(x)
        elif x in ("на",) and out and re.fullmatch(r"[\d.]+\)?", out[-1]):
            out.append("*")                                  # «5 на 6»
        else:
            return None                                      # посторонние слова — не наш случай
    expr = "".join(out)
    return expr if re.search(r"[-+*/(]", expr) else None


def _parse_calc(text: str):
    expr = to_expr(text)
    return {"expression": expr} if expr else None


@skill("calculate", "Калькулятор", "Посчитать: плюс, минус, умножить, разделить, проценты, квадрат, корень.",
       params=[Param("expression", "string", "выражение")],
       category="Информация", model=False, parse=_parse_calc)
def calculate(ctx, expression: str):
    try:
        r = _eval(ast.parse(expression, mode="eval"))
    except ZeroDivisionError:
        return "На ноль делить нельзя."
    except (ValueError, SyntaxError, OverflowError):
        return "Не получилось посчитать — скажите по-другому."
    return f"Будет {num(r)}."


# ——— курс валют ———

CURRENCY = {   # основа слова → код, формы для числа (1 / 2 / 5)
    "доллар": ("USD", "доллар", "доллара", "долларов"), "бакс": ("USD", "доллар", "доллара", "долларов"),
    "евро": ("EUR", "евро", "евро", "евро"), "юан": ("CNY", "юань", "юаня", "юаней"),
    "тенге": ("KZT", "тенге", "тенге", "тенге"), "фунт": ("GBP", "фунт", "фунта", "фунтов"),
    "иен": ("JPY", "иена", "иены", "иен"), "йен": ("JPY", "иена", "иены", "иен"),
    "гривн": ("UAH", "гривна", "гривны", "гривен"), "лир": ("TRY", "лира", "лиры", "лир"),
    "франк": ("CHF", "франк", "франка", "франков"),
    "белорусск": ("BYN", "белорусский рубль", "белорусских рубля", "белорусских рублей"),
    "рубл": ("RUB", "рубль", "рубля", "рублей"),
}
_rates: tuple[float, dict] = (0.0, {})


def _currencies(t: str) -> list[tuple[int, str]]:
    """[(позиция слова, код)] в порядке упоминания; «белорусских рублей» — один BYN."""
    words = t.split()
    out = []
    for i, w in enumerate(words):
        for stem, (code, *_) in CURRENCY.items():
            if w.startswith(stem) and not (stem == "рубл" and i and words[i - 1].startswith("белорусск")):
                out.append((i, code))
                break
    return out


def _parse_currency(text: str):
    t = _key(text)
    found = _currencies(t)
    if not found:
        return None
    words = t.split()
    nums = [(i, float(w)) for i, w in enumerate(words) if re.fullmatch(r"\d+(?:\.\d+)?", w)]
    codes = [c for _, c in found]
    if re.search(r"\bкурс\w*\b|сколько стоит|почем", t) and len(set(codes) - {"RUB"}) == 1 and not nums:
        return {"amount": 1.0, "src": next(c for c in codes if c != "RUB"), "dst": "RUB"}
    if not nums or len(set(codes)) != 2 or not re.search(r"\b(сколько|переведи|перевести|конвертируй|это|в)\b", t):
        return None
    if re.search(r"\b(сколько|почем)\b", t) and len(found) == 2:
        # «сколько долларов в 5000 рублей» — считаем то, что после числа, в то, что спросили
        (i1, c1), (i2, c2) = found
        n_pos = nums[0][0]
        src, dst = (c2, c1) if abs(i2 - n_pos) <= abs(i1 - n_pos) else (c1, c2)
    else:
        src, dst = codes[0], next(c for c in codes if c != codes[0])
    return {"amount": nums[0][1], "src": src, "dst": dst}


def rates() -> dict:
    """{код: рублей за 1 единицу}; кэш на час."""
    global _rates
    if time.monotonic() - _rates[0] < 3600 and _rates[1]:
        return _rates[1]
    data = _get("https://www.cbr-xml-daily.ru/daily_json.js")
    r = {code: v["Value"] / v["Nominal"] for code, v in data["Valute"].items()}
    r["RUB"] = 1.0
    _rates = (time.monotonic(), r)
    return r


def money(x: float, code: str) -> str:
    forms = next(f for _, (c, *f) in CURRENCY.items() if c == code)
    x = round(x, 2)
    n = int(x) if x == int(x) else 2                     # дробное — «2,5 доллара»
    return f"{num(x)} {plural(n, *forms)}"


@skill("currency", "Курс валют", "Курс ЦБ и перевод сумм между рублями и валютами.",
       params=[Param("amount", "number", "сумма"), Param("src", "string", "из какой валюты"),
               Param("dst", "string", "в какую")],
       category="Информация", model=False, parse=_parse_currency)
def currency(ctx, amount: float, src: str, dst: str):
    try:
        r = rates()
    except Exception as e:
        log.warning("курс: %s", e)
        return "Курс ЦБ не загрузился — нет связи с интернетом?"
    if src not in r or dst not in r:
        return "Такой валюты нет в курсах ЦБ."
    got = amount * r[src] / r[dst]
    if amount == 1 and dst == "RUB":
        return f"Курс ЦБ: {money(1, src).removeprefix('1 ')} — {money(got, 'RUB')}."
    return f"{money(amount, src)} — это {money(got, dst)} по курсу ЦБ."
