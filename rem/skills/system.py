"""Папки, скриншоты, окна, питание, время, таймеры."""
from __future__ import annotations

import datetime as dt
import os
import re

from .. import winapi
from ..text import duration_ru, elapsed_ru, plural
from .base import Param, skill

FOLDERS = ["desktop", "downloads", "documents", "pictures", "music", "videos"]
FOLDER_RU = {"desktop": "рабочий стол", "downloads": "загрузки", "documents": "документы",
             "pictures": "изображения", "music": "музыка", "videos": "видео"}


@skill("open_folder", "Открыть папку", "Открыть системную папку в проводнике.",
       params=[Param("folder", "string", "какая папка", enum=FOLDERS)],
       examples=[("открой загрузки", {"folder": "downloads"}),
                 ("открой папку загрузки", {"folder": "downloads"}),
                 ("открой документы", {"folder": "documents"}),
                 ("открой папку документы", {"folder": "documents"}),
                 ("открой рабочий стол", {"folder": "desktop"}),
                 ("открой изображения", {"folder": "pictures"}),
                 ("открой картинки", {"folder": "pictures"})],
       category="Файлы")
def open_folder(ctx, folder: str):
    winapi.open_path(winapi.known_folder(folder))


@skill("create_folder", "Создать папку", "Создать новую папку с указанным именем.",
       params=[Param("name", "string", "имя папки"),
               Param("where", "string", "где создать (по умолчанию рабочий стол)",
                     enum=FOLDERS, required=False)],
       category="Файлы")
def create_folder(ctx, name: str, where: str = "desktop"):
    clean = re.sub(r'[\\/:*?"<>|]', "", name).strip(" .")
    if not clean:
        return "Не получилось разобрать имя папки."
    path = os.path.join(winapi.known_folder(where or "desktop"), clean)
    if os.path.exists(path):
        return f"Папка «{clean}» уже есть."
    os.makedirs(path)
    return f"Папка «{clean}» создана: {FOLDER_RU.get(where or 'desktop', '')}."


@skill("screenshot", "Скриншот", "Сделать снимок экрана и сохранить в папку «Изображения».",
       examples=["скриншот", "сделай скриншот", "сфоткай экран", "снимок экрана"],
       category="Система")
def screenshot(ctx):
    from PIL import ImageGrab
    folder = os.path.join(winapi.known_folder("pictures"), "Screenshots")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, dt.datetime.now().strftime("Рэм %Y-%m-%d %H-%M-%S.png"))
    ImageGrab.grab(all_screens=True).save(path)


@skill("minimize_all", "Свернуть все окна", "Свернуть все окна и показать рабочий стол.",
       examples=["сверни все окна", "сверни все", "покажи рабочий стол"],
       category="Система")
def minimize_all(ctx):
    winapi.press(winapi.VK["win"], winapi.VK["d"])


@skill("lock_pc", "Заблокировать компьютер", "Заблокировать компьютер (экран входа).",
       examples=["заблокируй компьютер", "заблокируй комп", "блокировка"],
       category="Питание")
def lock_pc(ctx):
    winapi.lock_workstation()


@skill("sleep_pc", "Спящий режим", "Перевести компьютер в спящий режим.",
       examples=["спящий режим", "усыпи компьютер"], confirm=True, category="Питание")
def sleep_pc(ctx):
    winapi.sleep_pc()


@skill("shutdown_pc", "Выключить компьютер", "Выключить компьютер.",
       examples=["выключи компьютер", "выключи комп", "завершение работы"],
       confirm=True, category="Питание")
def shutdown_pc(ctx):
    winapi.shutdown(restart=False)


@skill("restart_pc", "Перезагрузить компьютер", "Перезагрузить компьютер.",
       examples=["перезагрузи компьютер", "перезагрузи комп", "перезагрузка"],
       confirm=True, category="Питание")
def restart_pc(ctx):
    winapi.shutdown(restart=True)


MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


@skill("tell_time", "Который час", "Сказать текущее время.",
       examples=["который час", "сколько времени", "сколько сейчас времени", "время"],
       category="Информация")
def tell_time(ctx, now: dt.datetime | None = None):
    now = now or dt.datetime.now()
    h, m = now.hour, now.minute
    hours = f"{h} {plural(h, 'час', 'часа', 'часов')}"
    return f"{hours} {m} {plural(m, 'минута', 'минуты', 'минут')}." if m else f"Ровно {hours}."


@skill("tell_date", "Какое сегодня число", "Сказать сегодняшнюю дату и день недели.",
       examples=["какое сегодня число", "какой сегодня день", "какая дата", "какое число"],
       category="Информация")
def tell_date(ctx, now: dt.datetime | None = None):
    now = now or dt.datetime.now()
    return f"Сегодня {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}."


@skill("set_timer", "Таймер", "Поставить таймер; по окончании прозвучит сигнал.",
       params=[Param("minutes", "integer", "минуты", minimum=0, maximum=600, required=False),
               Param("seconds", "integer", "секунды", minimum=0, maximum=3600, required=False)],
       examples=["таймер на {n} минут", "поставь таймер на {n} минут", "засеки {n} минут"],
       category="Информация")
def set_timer(ctx, minutes: int = 0, seconds: int = 0):
    total = int(minutes or 0) * 60 + int(seconds or 0)
    if total <= 0:
        return "На сколько поставить таймер?"
    label = duration_ru(total)
    ctx.timers.start(total, f"{elapsed_ru(total)} — таймер закончился.")
    return f"Таймер на {label}."


@skill("cancel_timers", "Отменить таймеры", "Отменить все запущенные таймеры.",
       examples=["отмени таймер", "отмени таймеры", "выключи таймер", "сбрось таймер"],
       category="Информация")
def cancel_timers(ctx):
    n = ctx.timers.cancel_all()
    return "Таймеры отменены." if n else "Таймеров нет."
