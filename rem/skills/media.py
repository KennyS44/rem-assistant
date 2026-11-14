"""Музыка, видео и громкость.

«Включи музыку», «в музыку» — запустить музыкальное приложение (Яндекс Музыка, Spotify…) и включить
воспроизведение; если что-то уже стоит на паузе — продолжить. Что играет, Рэм узнаёт у Windows
(медиасессии — то же, что плитка музыки у регулятора громкости), поэтому «включи» не ставит на паузу."""
import re
import time

from .. import winapi
from ..text import norm
from .base import Param, skill


@skill("media_play_pause", "Пауза / продолжить",
       "Поставить на паузу или продолжить воспроизведение музыки или видео.",
       examples=["пауза", "поставь на паузу", "продолжи", "плей", "поставь паузу", "продолжи воспроизведение",
                 "продолжай", "играй дальше", "сними с паузы", "возобнови"],
       category="Медиа")
def media_play_pause(ctx):
    winapi.press(winapi.VK["media_play_pause"])


@skill("media_next", "Следующий трек", "Следующий трек или видео.",
       examples=["следующий трек", "следующая песня", "дальше", "переключи трек", "следующее", "следующая",
                 "следующую песню", "переключи песню", "следующее видео", "другую песню", "включи следующую песню",
                 "включи следующий трек", "следующий"],
       category="Медиа")
def media_next(ctx):
    winapi.press(winapi.VK["media_next"])


@skill("media_prev", "Предыдущий трек", "Предыдущий трек или видео.",
       examples=["предыдущий трек", "предыдущая песня", "назад трек", "верни прошлый трек", "предыдущая",
                 "предыдущую песню", "прошлая песня", "прошлую песню", "предыдущее видео", "включи предыдущую песню",
                 "верни прошлую песню"],
       category="Медиа")
def media_prev(ctx):
    winapi.press(winapi.VK["media_prev"])


@skill("volume_set", "Громкость: уровень", "Установить громкость звука в процентах.",
       params=[Param("level", "integer", "громкость 0–100", minimum=0, maximum=100)],
       examples=["громкость {n}", "громкость на {n}", "громкость {n} процентов",
                 "сделай громкость {n}", "поставь громкость {n}", "звук {n}",
                 *[(p, {"level": 100}) for p in ("громкость на максимум", "на полную громкость", "громкость на полную",
                                                 "сделай громкость на максимум", "максимальная громкость")]],
       category="Медиа")
def volume_set(ctx, level: int):
    winapi.set_volume(int(level))


@skill("volume_up", "Громче", "Сделать звук громче (на 10%).",
       examples=["громче", "сделай громче", "сделай погромче", "прибавь звук", "погромче", "добавь громкость",
                 "сделай звук громче", "звук громче", "громче звук", "добавь звук", "прибавь громкость",
                 "сделай звук погромче", "еще громче", "чуть громче", "немного громче"],
       category="Медиа")
def volume_up(ctx):
    winapi.set_volume(winapi.get_volume() + 10)


@skill("volume_down", "Тише", "Сделать звук тише (на 10%).",
       examples=["тише", "сделай тише", "сделай потише", "убавь звук", "потише", "убавь громкость",
                 "сделай звук тише", "звук тише", "тише звук", "сделай звук потише", "еще тише", "чуть тише",
                 "немного тише"],
       category="Медиа")
def volume_down(ctx):
    winapi.set_volume(winapi.get_volume() - 10)


@skill("volume_mute", "Выключить / включить звук", "Выключить звук или включить его обратно.",
       examples=["выключи звук", "без звука", "включи звук", "заглуши звук", "верни звук", "убери звук", "отключи звук"],
       category="Медиа")
def volume_mute(ctx):
    winapi.press(winapi.VK["volume_mute"])


# ——— «включи музыку» ———

_MUSIC = r"(?:музык\w*|музон\w*|песн(?:и|ю)|яндекс музык\w*|яндекс\.музык\w*|музык\w* яндекс\w*)"
_PLAY = re.compile(rf"^(?:(?:включи|поставь|запусти|врубай|вруби|давай|хочу|играй|сыграй)\s+(?:(?:мне|какую-?нибудь|"
                   rf"приложение|программу)\s+)*|(?:перейди\s+|иди\s+|переключись\s+)?(?:в|на)\s+)?{_MUSIC}$")
_OPEN = re.compile(rf"^(?:открой|покажи|разверни)\s+(?:приложение\s+)?{_MUSIC}$")
_STOP = re.compile(rf"^(?:выключи|останови|отключи|выруби|приостанови|поставь на паузу|пауза)\s+{_MUSIC}$|"
                   rf"^{_MUSIC}\s+(?:на паузу|стоп)$|^стоп\s+{_MUSIC}$")


def _parse_music(text: str):
    t = norm(text).replace("ё", "е")
    t = " ".join(w for w in t.split() if w not in ("пожалуйста", "ка", "мне", "ну", "рэм"))
    if _STOP.match(t):
        return {"action": "stop"}
    if _OPEN.match(t):
        return {"action": "open"}
    if _PLAY.match(t):
        return {"action": "play"}
    return None


def _sessions() -> list[dict]:
    try:
        return winapi.media_sessions()
    except Exception:
        return []


def _is_music(app_id: str, name: str) -> bool:
    """Медиасессия принадлежит музыкальному приложению name (AppUserModelId: «ru.yandex.desktop.music»…)."""
    a = app_id.lower()
    words = [w for w in re.split(r"[\s.]+", _latin(name)) if len(w) > 2]
    return any(w in a for w in words) or "music" in a or "spotify" in a


def _latin(name: str) -> str:
    """«яндекс музыка» → «яндекс музыка yandex music»: AppUserModelId пишут латиницей."""
    from ..text import translit
    n = norm(name)
    return n + " " + translit(n).replace("yandeks", "yandex").replace("muzyka", "music")


@skill("music", "Включить музыку",
       "Включить музыку: запустить музыкальное приложение (Яндекс Музыка, Spotify) и воспроизведение; "
       "открыть его; остановить музыку.",
       params=[Param("action", "string", "что сделать", enum=["play", "open", "stop"])],
       category="Медиа", model=False, parse=_parse_music)
def music(ctx, action: str):
    sessions = _sessions()
    playing = [s for s in sessions if s["status"] == "Playing"]
    if action == "stop":
        if not playing:
            return "Музыка и так не играет."
        for s in playing:
            winapi.media_command(s["app"], "pause")
        return None
    app = ctx.apps.music()
    if action == "play" and playing:
        return "Музыка уже играет."
    if app is None:
        if action == "play" and sessions:                # своего приложения нет — продолжить то, что на паузе
            winapi.media_command(sessions[0]["app"], "play")
            return None
        winapi.open_path("https://music.yandex.ru")
        return ctx.phrase("Музыкального приложения не нашла — открыла Яндекс Музыку в браузере.",
                          "Рэм не нашла музыкальное приложение и открыла Яндекс Музыку в браузере.")
    mine = [s for s in sessions if _is_music(s["app"], app[0])]
    if action == "play" and mine:                        # приложение уже открыто и стоит на паузе
        if winapi.media_command(mine[0]["app"], "play"):
            return None
    before = winapi.foreground_window()
    winapi.open_path(app[1])                             # запущено — Windows просто покажет его окно
    if action == "open":
        return None
    # ждём окно приложения, затем «играть»: приложение продолжит с того места, где остановились
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        time.sleep(0.5)
        w = winapi.foreground_window()
        if w and before and w["hwnd"] != before["hwnd"]:
            break
    if not mine:
        time.sleep(3)                                    # только что запустилось — даём загрузиться
    winapi.press(winapi.VK["media_play_pause"])          # одно нажатие: второе поставило бы на паузу
    now = []
    for _ in range(3):
        time.sleep(1)
        now = _sessions()
        if any(s["status"] == "Playing" for s in now):
            return None
    mine = [s for s in now if _is_music(s["app"], app[0])]
    if mine and winapi.media_command(mine[0]["app"], "play"):   # кнопка «играть» именно в этом приложении
        return None
    if not now:                                          # Windows не рассказывает, что играет, — верим нажатию
        return None
    return ctx.phrase("Открыла музыку — нажмите «Слушать» в приложении.",
                      "Рэм открыла музыку — нажмите «Слушать» в приложении.")
