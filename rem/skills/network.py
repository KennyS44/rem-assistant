"""Сеть: включить и выключить Wi‑Fi и Bluetooth, к какой сети подключён компьютер, есть ли интернет.
Только быстрым путём (разбор фразы): модель этого умения не видит.

Wi‑Fi и Bluetooth переключаются как кнопками в центре уведомлений (Windows.Devices.Radios) —
права администратора не нужны. Наушники, сопряжённые раньше, после включения Bluetooth
обычно подключаются сами."""
from __future__ import annotations

import re
import socket
import time

from .. import winapi
from .base import Param, skill

ACTIONS = ["wifi_on", "wifi_off", "bt_on", "bt_off", "which", "internet"]
KIND = {"wifi": "WiFi", "bt": "Bluetooth"}
NAME = {"wifi": "Wi‑Fi", "bt": "Bluetooth"}

_WIFI = r"(?:wi fi|wifi|вай фай|вайфай|wi-fi|вай-фай|беспроводн\w+ сеть)"
_BT = r"(?:bluetooth|блютуз|блютус|блю туз|блутуз)"
_ON = r"(?:включи|вруби|подключи|активируй|запусти)"
_OFF = r"(?:выключи|отключи|выруби|деактивируй|отруби)"
_RULES = [
    ("wifi_on", re.compile(rf"^{_ON}\s+{_WIFI}$")),
    ("wifi_off", re.compile(rf"^{_OFF}\s+{_WIFI}$")),
    ("bt_on", re.compile(rf"^{_ON}\s+{_BT}$")),
    ("bt_off", re.compile(rf"^{_OFF}\s+{_BT}$")),
    ("which", re.compile(rf"^(?:к\s+)?(?:какой|какому)\s+(?:сети|{_WIFI}|вайфаю)\s+(?:я\s+|мы\s+|комп\w*\s+)?"
                         r"подключ\w+$|"
                         rf"^(?:какая|как называется)\s+(?:сеть|моя сеть|{_WIFI})$|"
                         rf"^(?:через\s+)?какую\s+сеть\s+(?:я\s+)?(?:подключ\w+|работаю|сижу)$|"
                         rf"^(?:какой|мой)\s+{_WIFI}$")),
    ("internet", re.compile(r"^(?:есть\s+ли|работает\s+ли|а\s+есть|у\s+меня\s+есть)\s+(?:интернет|сеть|связь)$|"
                            r"^(?:интернет|сеть)\s+(?:есть|работает)$|"
                            r"^(?:проверь|проверка)\s+(?:интернет\w*|сеть|связь|подключени\w+)$|"
                            r"^почему\s+(?:не\s+работает|нет|пропал)\s+интернет$|"
                            r"^(?:интернет|сеть)\s+(?:пропал\w*|не\s+работает|отвалил\w*|лежит)$")),
]


def _parse(text: str):
    from ..fastpath import clean                     # fastpath сам импортирует умения
    t = clean(text).replace("-", " ")
    for action, rx in _RULES:
        if rx.match(t):
            return {"action": action}
    return None


def check_internet(timeout: float = 3.0) -> tuple[bool, bool, int]:
    """(есть связь с интернетом, работает DNS, отклик в мс)."""
    started = time.monotonic()
    link = False
    for host in ("1.1.1.1", "8.8.8.8", "77.88.8.8"):
        try:
            socket.create_connection((host, 443), timeout=timeout).close()
            link = True
            break
        except OSError:
            continue
    ms = round((time.monotonic() - started) * 1000)
    try:
        socket.getaddrinfo("ya.ru", 443)
        dns = True
    except OSError:
        dns = False
    return link, dns, ms


def internet_text() -> str:
    link, dns, ms = check_internet()
    if link and dns:
        return f"Интернет есть, отклик {ms} мс." if ms >= 150 else "Интернет есть и работает быстро."
    if link:
        return "Связь есть, но не работает DNS — сайты по именам не открываются. Помогает перезагрузка роутера."
    if dns:
        return "Интернет еле работает: сайты не отвечают. Попробуйте через минуту."
    return "Интернета нет."


def which_text() -> str:
    conns = winapi.connections()
    if not conns:
        return "Компьютер не подключён ни к одной сети."
    parts = []
    for c in conns:
        alias = c["alias"].lower()
        how = "по Wi‑Fi" if re.search(r"wi.?fi|wlan|беспровод", alias) else (
            "по кабелю" if re.search(r"ethernet|подключение по локальной|lan", alias) else f"через «{c['alias']}»")
        parts.append(f"{how} к сети «{c['name']}»" + ("" if c["internet"] else " (без интернета)"))
    return "Подключено " + "; ".join(parts) + "."


@skill("network", "Сеть: Wi‑Fi, Bluetooth, интернет",
       "Включить или выключить Wi‑Fi и Bluetooth, к какой сети подключён компьютер, есть ли интернет.",
       params=[Param("action", "string", "что сделать", enum=ACTIONS)],
       category="Система", model=False, parse=_parse)
def network(ctx, action: str):
    if action == "internet":
        return internet_text()
    if action == "which":
        return which_text()
    kind, state = action.split("_")
    radio = KIND[kind]
    got = winapi.radios(radio, "On" if state == "on" else "Off")
    if radio not in got:
        return f"{NAME[kind]} на этом компьютере нет."
    if got[radio] != ("On" if state == "on" else "Off"):
        return f"{NAME[kind]} не переключился — возможно, он выключен кнопкой или в режиме «в самолёте»."
    return f"{NAME[kind]} {'включён' if state == 'on' else 'выключен'}."
