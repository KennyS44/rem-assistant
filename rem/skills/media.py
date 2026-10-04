"""Музыка, видео и громкость."""
from .. import winapi
from .base import Param, skill


@skill("media_play_pause", "Пауза / продолжить",
       "Поставить на паузу или продолжить воспроизведение музыки или видео.",
       examples=["пауза", "поставь на паузу", "продолжи", "стоп музыка", "включи музыку", "плей"],
       category="Медиа")
def media_play_pause(ctx):
    winapi.press(winapi.VK["media_play_pause"])


@skill("media_next", "Следующий трек", "Следующий трек или видео.",
       examples=["следующий трек", "следующая песня", "дальше", "переключи трек", "следующее"],
       category="Медиа")
def media_next(ctx):
    winapi.press(winapi.VK["media_next"])


@skill("media_prev", "Предыдущий трек", "Предыдущий трек или видео.",
       examples=["предыдущий трек", "предыдущая песня", "назад трек", "верни прошлый трек"],
       category="Медиа")
def media_prev(ctx):
    winapi.press(winapi.VK["media_prev"])


@skill("volume_set", "Громкость: уровень", "Установить громкость звука в процентах.",
       params=[Param("level", "integer", "громкость 0–100", minimum=0, maximum=100)],
       examples=["громкость {n}", "громкость на {n}", "громкость {n} процентов",
                 "сделай громкость {n}", "поставь громкость {n}"],
       category="Медиа")
def volume_set(ctx, level: int):
    winapi.set_volume(int(level))


@skill("volume_up", "Громче", "Сделать звук громче (на 10%).",
       examples=["громче", "сделай громче", "сделай погромче", "прибавь звук", "погромче", "добавь громкость"],
       category="Медиа")
def volume_up(ctx):
    winapi.set_volume(winapi.get_volume() + 10)


@skill("volume_down", "Тише", "Сделать звук тише (на 10%).",
       examples=["тише", "сделай тише", "сделай потише", "убавь звук", "потише", "убавь громкость"],
       category="Медиа")
def volume_down(ctx):
    winapi.set_volume(winapi.get_volume() - 10)


@skill("volume_mute", "Выключить / включить звук", "Выключить звук или включить его обратно.",
       examples=["выключи звук", "без звука", "включи звук", "заглуши звук"],
       category="Медиа")
def volume_mute(ctx):
    winapi.press(winapi.VK["volume_mute"])
