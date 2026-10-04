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
