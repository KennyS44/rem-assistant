import pytest

from rem.config import DEFAULTS
from rem.fastpath import FastPath, words_to_digits
from rem.skills import active_skills


@pytest.fixture(scope="module")
def fp():
    return FastPath(active_skills(DEFAULTS))


@pytest.mark.parametrize("text,skill,args", [
    ("громче", "volume_up", {}),
    ("Сделай потише, пожалуйста.", "volume_down", {}),
    ("громкость 30", "volume_set", {"level": 30}),
    ("Сделай громкость на 30 процентов", "volume_set", {"level": 30}),
    ("громкость тридцать пять", "volume_set", {"level": 35}),
    ("поставь громкость 70%", "volume_set", {"level": 70}),
    ("пауза", "media_play_pause", {}),
    ("Следующий трек.", "media_next", {}),
    ("который час", "tell_time", {}),
    ("Сколько сейчас времени?", "tell_time", {}),
    ("сделай скриншот", "screenshot", {}),
    ("открой загрузки", "open_folder", {"folder": "downloads"}),
    ("Открой рабочий стол.", "open_folder", {"folder": "desktop"}),
    ("таймер на 5 минут", "set_timer", {"minutes": 5}),
    ("открой ютуб", "open_site", {"url": "youtube.com"}),
    ("открой браузер", "open_app", {"name": "браузер"}),
    ("открой хром", "open_app", {"name": "хром"}),
    ("закрой хром", "close_app", {"name": "хром"}),
    ("открой калькулятор", "open_app", {"name": "калькулятор"}),
    ("выключи звук", "volume_mute", {}),
    ("выключи компьютер", "shutdown_pc", {}),
    ("сделай скриншод", "screenshot", {}),          # ошибка распознавания
])
def test_fast_hits(fp, text, skill, args):
    plan = fp.match(text)
    assert plan is not None, text
    name, got = plan.actions[0]
    assert name == skill
    assert got == args


@pytest.mark.parametrize("text", [
    "громкость 300",                 # вне диапазона
    "открой мне что-нибудь интересное",
    "найди рецепт борща",            # поиск — решает модель
    "создай папку отчёты",
    "выключи музыку",                # не «выключи звук» и не программа
    "закрой загрузки",               # «закрой» ≠ «открой»
    "расскажи анекдот",
    "",
])
def test_fast_misses_go_to_model(fp, text):
    assert fp.match(text) is None


def test_words_to_digits():
    assert words_to_digits("громкость двадцать пять") == "громкость 25"
    assert words_to_digits("таймер на пять минут") == "таймер на 5 минут"
    assert words_to_digits("сто") == "100"
    assert words_to_digits("тридцать сорок") == "30 40"
