"""Сайты и поиск в интернете."""
import re
from urllib.parse import quote_plus

from .. import winapi
from ..text import norm
from .base import Param, skill

SITES = {
    "ютуб": "youtube.com", "youtube": "youtube.com", "вк": "vk.com", "вконтакте": "vk.com",
    "гугл": "google.com", "яндекс": "ya.ru", "почта": "mail.google.com", "гмейл": "mail.google.com",
    "твич": "twitch.tv", "гитхаб": "github.com", "википедия": "ru.wikipedia.org",
    "кинопоиск": "kinopoisk.ru", "авито": "avito.ru", "озон": "ozon.ru", "вайлдберриз": "wildberries.ru",
    "переводчик": "translate.yandex.ru", "карты": "yandex.ru/maps", "погода": "yandex.ru/pogoda",
}

ENGINES = {
    "google": "https://www.google.com/search?q={}",
    "yandex": "https://yandex.ru/search/?text={}",
}


def site_url(site: str) -> str:
    s = site.strip()
    if re.match(r"^https?://", s, re.I):
        return s
    known = SITES.get(norm(s).removeprefix("сайт "))
    if known:
        s = known
    if "." not in s:                      # не адрес — ищем по названию
        return ENGINES["google"].format(quote_plus(site))
    return "https://" + s.replace(" ", "")


@skill("open_site", "Открыть сайт",
       "Открыть сайт в браузере. url — адрес сайта, например youtube.com, vk.com, ozon.ru.",
       params=[Param("url", "string", "адрес сайта")],
       examples=["открой {site}", "зайди на {site}", "открой сайт {site}"],
       category="Интернет")
def open_site(ctx, url: str):
    winapi.open_path(site_url(url))


@skill("web_search", "Поиск в интернете",
       "Найти что-то в интернете — открывает поиск в браузере. query — поисковый запрос.",
       params=[Param("query", "string", "что искать")],
       examples=["найди {q}", "загугли {q}", "поищи {q}"],
       category="Интернет")
def web_search(ctx, query: str):
    engine = ENGINES.get(ctx.config.get("search_engine", "google"), ENGINES["google"])
    winapi.open_path(engine.format(quote_plus(query)))


# ——— YouTube ———

YT = r"(?:ютуб\w*|youtube|ютьюб\w*)"
_YT_RULES = [   # «включи на ютубе X», «найди на ютубе X», «включи X на ютубе», «включи видео X»
    re.compile(rf"^(?P<verb>включи|поставь|запусти|открой|покажи|найди|поищи|ищи|загугли)\s+(?:на|в)\s+{YT}\s+(?P<q>.+)$"),
    re.compile(rf"^(?P<verb>включи|поставь|запусти|открой|покажи|найди|поищи)\s+(?P<q>.+?)\s+(?:на|в|с)\s+{YT}$"),
    re.compile(r"^(?P<verb>включи|поставь|запусти|покажи|найди|поищи)\s+(?:видео|видос\w*|ролик)\s+(?P<q>.+)$"),
]
PLAY_VERBS = {"включи", "поставь", "запусти", "открой", "покажи"}
_VIDEO = re.compile(r'"videoRenderer":\{"videoId":"([\w-]{11})"')
_TITLE = re.compile(r'"videoRenderer":\{"videoId":"[\w-]{11}".*?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', re.S)


def _parse_youtube(text: str):
    t = norm(text)
    for rx in _YT_RULES:
        m = rx.match(t)
        if m and m.group("q").strip():
            return {"query": m.group("q").strip(), "play": "да" if m.group("verb") in PLAY_VERBS else "нет"}
    return None


def youtube_search_url(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(query)


def first_video(query: str, timeout: float = 6.0) -> tuple[str, str] | None:
    """Первое видео из поиска YouTube — (адрес, название) или None (нет связи, YouTube не ответил).
    Без ключа и регистрации: страница поиска содержит список видео."""
    import urllib.request
    req = urllib.request.Request(youtube_search_url(query), headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "ru,en;q=0.8"})
    try:
        page = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception:
        return None
    return parse_first_video(page)


def parse_first_video(page: str) -> tuple[str, str] | None:
    import json
    m = _VIDEO.search(page)
    if not m:
        return None
    t = _TITLE.search(page, m.start())
    try:
        title = json.loads(f'"{t.group(1)}"') if t else ""
    except ValueError:
        title = ""
    return f"https://www.youtube.com/watch?v={m.group(1)}", title


@skill("youtube", "Видео на YouTube",
       "Найти на YouTube или сразу включить первое найденное видео.",
       params=[Param("query", "string", "что найти"),
               Param("play", "string", "сразу включить первое видео", enum=["да", "нет"], required=False)],
       category="Интернет", model=False, parse=_parse_youtube)
def youtube(ctx, query: str, play: str = "да"):
    if play == "да":
        hit = first_video(query)
        if hit:
            url, title = hit
            winapi.open_path(url)
            if title:
                ctx.show(f"YouTube: {title}")
            return None
        winapi.open_path(youtube_search_url(query))
        return "Видео сразу не нашлось — открыт поиск на YouTube."
    winapi.open_path(youtube_search_url(query))
    return None
