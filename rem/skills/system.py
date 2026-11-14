"""Папки, скриншоты, окна, питание, время, таймеры."""
from __future__ import annotations

import datetime as dt
import os
import re

from .. import winapi
from ..text import duration_ru, elapsed_ru, norm, plural
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
        return ctx.phrase("Не получилось разобрать имя папки.", "Простите, Рэм не разобрала имя папки.")
    path = os.path.join(winapi.known_folder(where or "desktop"), clean)
    if os.path.exists(path):
        return f"Папка «{clean}» уже есть."
    os.makedirs(path)
    return f"Папка «{clean}» создана: {FOLDER_RU.get(where or 'desktop', '')}."


@skill("screenshot", "Скриншот", "Сделать снимок экрана и сохранить в папку «Изображения».",
       examples=["скриншот", "сделай скриншот", "сфоткай экран", "снимок экрана", "сделай скриншот экрана",
                 "скрин", "сделай скрин", "заскринь", "заскринь экран", "сделай снимок экрана"],
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
       examples=["заблокируй компьютер", "заблокируй комп", "блокировка", "заблокируй экран", "заблокируй",
                 "заблокируй ноутбук"],
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
       examples=["который час", "сколько времени", "сколько сейчас времени", "время", "сколько время",
                 "который сейчас час", "скажи время", "подскажи время", "сколько на часах", "сколько сейчас время",
                 "скажи сколько времени", "подскажи который час"],
       category="Информация")
def tell_time(ctx, now: dt.datetime | None = None):
    now = now or dt.datetime.now()
    h, m = now.hour, now.minute
    hours = f"{h} {plural(h, 'час', 'часа', 'часов')}"
    return f"{hours} {m} {plural(m, 'минута', 'минуты', 'минут')}." if m else f"Ровно {hours}."


@skill("tell_date", "Какое сегодня число", "Сказать сегодняшнюю дату и день недели.",
       examples=["какое сегодня число", "какой сегодня день", "какая дата", "какое число", "какое число сегодня",
                 "какой сегодня день недели", "какой день недели", "какой сегодня день недели и число",
                 "какая сегодня дата", "скажи дату", "какое сегодня число и день недели"],
       category="Информация")
def tell_date(ctx, now: dt.datetime | None = None):
    now = now or dt.datetime.now()
    return f"Сегодня {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}."


_TIMER = re.compile(r"^(?:(?:поставь|заведи|включи|запусти|сделай|установи)\s+)?(?:таймер|засеки|отсчет)\s+"
                    r"(?:на\s+)?(?P<d>.+)$")
_UNIT = {"сек": 1, "мин": 60, "час": 3600}


def parse_duration(text: str) -> int | None:
    """«полчаса» → 1800, «1 час 20 минут» → 4800, «2 минуты 30 секунд» → 150, «5» → 300 (минуты)."""
    t = text.replace("полтора часа", "90 минут").replace("полторы минуты", "90 секунд")
    t = t.replace("полчаса", "30 минут").replace("пол часа", "30 минут").replace("пару", "2").replace("пара", "2")
    words, total, num = t.split(), 0, None
    for w in words:
        if w.isdigit():
            num = int(w)
            continue
        unit = next((v for k, v in _UNIT.items() if w.startswith(k)), None)
        if unit is None:
            return None                             # лишние слова — пусть решает модель
        total += (1 if num is None else num) * unit   # «час», «минуту» — одна
        num = None
    if num is not None:
        if total:
            return None
        total = num * 60                            # «таймер на 5» — минуты
    return total or None


def _parse_timer(text: str):
    from ..fastpath import clean                     # fastpath сам импортирует умения
    m = _TIMER.match(clean(text))
    if not m:
        return None
    total = parse_duration(m.group("d"))
    if not total or total > 600 * 60:
        return None
    args = {"minutes": total // 60, "seconds": total % 60}
    return {k: v for k, v in args.items() if v}


@skill("set_timer", "Таймер", "Поставить таймер; по окончании прозвучит сигнал.",
       params=[Param("minutes", "integer", "минуты", minimum=0, maximum=600, required=False),
               Param("seconds", "integer", "секунды", minimum=0, maximum=3600, required=False)],
       examples=["таймер на {n} минут", "поставь таймер на {n} минут", "засеки {n} минут"],
       category="Информация", parse=_parse_timer)
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


# ——— «сообщи об ошибке» ———

_REPORT = re.compile(r"^(?:сообщи\s+(?:об|о|про)\s+(?:ошибк\w*|проблем\w*|баг\w*)|"
                     r"(?:отправь|пошли)\s+отчет\w*(?:\s+об\s+ошибк\w*)?)"
                     r"(?:\s+разработчику)?(?:\s+(?P<note>.+))?$")


def _parse_report(text: str):
    m = _REPORT.match(norm(text).replace("ё", "е"))
    return {"note": (m.group("note") or "").strip()} if m else None


@skill("report_problem", "Сообщить об ошибке",
       "Открыть заполненную заявку разработчику: последние команды и ошибки.",
       params=[Param("note", "string", "что случилось", required=False)],
       category="Система", model=False, parse=_parse_report)
def report_problem(ctx, note: str = ""):
    from .. import __version__
    from ..config import app_dir
    from ..report import open_report
    winapi.open_path(str(open_report(__version__, app_dir(), note)))
    return ctx.phrase("Открыла заявку в браузере — проверьте текст и нажмите «Submit new issue».",
                      "Рэм открыла заявку в браузере — проверьте текст и нажмите «Submit new issue».")


# ——— вежливость без модели: «спасибо», «привет» — ответ сразу ———

TALK = {
    "thanks": (["спасибо", "спасибо большое", "благодарю", "спасибо рэм", "молодец", "умница", "отлично спасибо"],
               "Пожалуйста!", "Рэм рада помочь."),
    "hello": (["привет", "здравствуй", "здравствуйте", "добрый день", "добрый вечер", "доброе утро", "приветик"],
              "Здравствуйте! Слушаю.", "Здравствуйте! Рэм слушает вас."),
    "how": (["как дела", "как ты", "как поживаешь", "как настроение", "ты тут", "ты здесь", "ты меня слышишь"],
            "Всё хорошо, слушаю.", "У Рэм всё хорошо. Рэм слушает."),
    "night": (["спокойной ночи", "доброй ночи"], "Спокойной ночи!", "Спокойной ночи. Рэм будет рядом."),
}


@skill("small_talk", "Вежливость", "Ответить на «спасибо», «привет», «как дела».",
       params=[Param("kind", "string", "что сказали", enum=list(TALK))],
       examples=[(p, {"kind": k}) for k, (phrases, _, _) in TALK.items() for p in phrases],
       category="Информация", model=False)
def small_talk(ctx, kind: str):
    _, plain, rem = TALK[kind]
    return ctx.phrase(plain, rem)
