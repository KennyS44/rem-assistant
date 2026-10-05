"""«Мозг»: превращает свободную фразу в действия через модель в Ollama.

Модель — на компьютере (config["model"]) или облачная модель Ollama (config["cloud_model"],
нужен вход в аккаунт Ollama). Облачная умнее и не занимает видеокарту; без интернета или
при исчерпанном лимите Рэм сама переходит на модель на компьютере.

Два режима:
  * tools (по умолчанию) — родной вызов функций модели. На наборе из 45 команд
    Qwen3 4B даёт 45/45 против 32/45 в режиме schema: так её обучали;
  * schema — ответ строго по JSON-схеме (запасной вариант для других моделей).
В обоих режимах каждый вызов проверяется (validate), а системный промпт неизменен
между командами — Ollama держит его в кэше и обрабатывает только слова команды.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .skills import Skill

log = logging.getLogger("rem.brain")

DEFAULT_URL = "http://127.0.0.1:11434"


@dataclass
class Plan:
    actions: list[tuple[str, dict]] = field(default_factory=list)   # (умение, аргументы)
    reply: str = ""
    source: str = "llm"           # llm | cloud | fast | game
    seconds: float = 0.0
    error: str = ""
    model: str = ""               # какая модель ответила — продолжение идёт к ней же
    user: str = ""                # что ушло модели (команда со справкой)
    history: list = field(default_factory=list)   # шаги после команды: вызовы и их результаты


class OllamaError(RuntimeError):
    pass


class Ollama:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 60):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, body: dict, timeout: float | None = None) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise OllamaError(f"Ollama ответила {e.code}: {e.read().decode(errors='replace')[:200]}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise OllamaError(f"Ollama недоступна: {e}")

    def _get(self, path: str) -> dict:
        try:
            with urllib.request.urlopen(self.url + path, timeout=5) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise OllamaError(f"Ollama недоступна: {e}")

    def available(self) -> bool:
        try:
            self._get("/api/version")
            return True
        except OllamaError:
            return False

    def start_local(self, wait: float = 30.0) -> bool:
        """Ollama установлена, но не запущена — запускаем её сами, без окон.
        Сначала фоновое приложение Ollama (оно же следит за обновлениями). Если оно
        не подняло сервер за 8 с (так бывает при первом запуске — оно ждёт приветствия),
        запускаем сам сервер. True — Ollama отвечает."""
        if self.available():
            return True
        import os
        import subprocess
        import sys
        if sys.platform != "win32":
            return False
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")
        app, cli = os.path.join(base, "ollama app.exe"), os.path.join(base, "ollama.exe")
        no_window = 0x08000000                                   # CREATE_NO_WINDOW

        def wait_up(seconds: float) -> bool:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if self.available():
                    return True
                time.sleep(1)
            return False

        try:
            if os.path.exists(app):
                subprocess.Popen([app], creationflags=no_window)
                if wait_up(8):
                    return True
            if not os.path.exists(cli):
                return False
            log.info("приложение Ollama не подняло сервер — запускаю ollama serve")
            subprocess.Popen([cli, "serve"], creationflags=no_window)
        except OSError:
            return False
        return wait_up(wait)

    def signin_url(self) -> str | None:
        """None — Ollama вошла в аккаунт (облачные модели доступны); иначе ссылка для входа."""
        try:
            self._post("/api/me", {}, timeout=10)
            return None
        except OllamaError as e:
            m = re.search(r'"signin_url":\s*"([^"]+)"', str(e))
            if m:
                return json.loads(f'"{m.group(1)}"')
            raise

    def models(self) -> list[str]:
        return [m["name"] for m in self._get("/api/tags").get("models", [])]

    def loaded(self) -> list[dict]:
        """Загруженные модели; size_vram > 0 — модель на видеокарте."""
        return self._get("/api/ps").get("models", [])

    def unload(self, model: str) -> None:
        self._post("/api/generate", {"model": model, "keep_alive": 0}, timeout=15)

    def warm(self, model: str, keep_alive: str, cpu_only: bool = False) -> None:
        """Загрузить модель заранее, без генерации."""
        body = {"model": model, "keep_alive": keep_alive}
        if cpu_only:
            body["options"] = {"num_gpu": 0}
        self._post("/api/generate", body, timeout=120)

    def pull(self, model: str, progress=None) -> None:
        """Скачать модель; progress(доля 0..1, статус)."""
        req = urllib.request.Request(self.url + "/api/pull",
                                     data=json.dumps({"model": model, "stream": True}).encode())
        with urllib.request.urlopen(req, timeout=3600) as r:
            for line in r:
                ev = json.loads(line)
                if "error" in ev:
                    raise OllamaError(ev["error"])
                if progress and ev.get("total"):
                    progress(ev.get("completed", 0) / ev["total"], ev.get("status", ""))

    def chat(self, model: str, system: str, user: str, schema: dict, *,
             keep_alive: str = "3m", cpu_only: bool = False, think: bool | None = False) -> dict:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 160},
        }
        if think is not None:
            body["think"] = think
        if cpu_only:
            body["options"]["num_gpu"] = 0
        try:
            out = self._post("/api/chat", body)
        except OllamaError as e:
            # модели без режима рассуждений отвечают ошибкой на параметр think
            if think is not None and "think" in str(e).lower():
                body.pop("think")
                out = self._post("/api/chat", body)
            else:
                raise
        return json.loads(out["message"]["content"])


    def chat_tools(self, model: str, system: str, user: str, tools: list[dict], *,
                   keep_alive: str = "3m", cpu_only: bool = False, think: bool | None = False,
                   timeout: float | None = None, extra: list | None = None) -> dict:
        """Родной вызов функций модели → {"actions": [...], "reply": текст}.
        extra — продолжение диалога: прошлые вызовы и их результаты (многошаговые задачи)."""
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}] + (extra or []),
            "tools": tools,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 300},
        }
        if think is not None:
            body["think"] = think
        if cpu_only:
            body["options"]["num_gpu"] = 0
        try:
            out = self._post("/api/chat", body, timeout)
        except OllamaError as e:
            if think is not None and "think" in str(e).lower():
                body.pop("think")
                out = self._post("/api/chat", body, timeout)
            else:
                raise
        msg = out.get("message", {})
        actions = []
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            actions.append({"skill": fn.get("name"), "args": args})
        reply = msg.get("content") or ""
        return {"actions": actions or text_calls(reply), "reply": "" if not actions and text_calls(reply) else reply}

    def ask(self, model: str, system: str, user: str, timeout: float | None = None) -> str:
        """Просто ответ текстом — пересказать вывод команды."""
        body = {"model": model, "stream": False, "think": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 120}}
        try:
            out = self._post("/api/chat", body, timeout)
        except OllamaError as e:
            if "think" not in str(e).lower():
                raise
            body.pop("think")
            out = self._post("/api/chat", body, timeout)
        return (out.get("message") or {}).get("content") or ""


CALL = re.compile(r"^\s*(\w+)\((.*?)\)\s*$")
ARG = re.compile(r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)'|(-?\d+))""")


def text_calls(reply: str) -> list[dict]:
    """Модель иногда пишет вызов текстом — «volume_mute()» — вместо настоящего вызова.
    Такой ответ разбираем как вызов; что это за умение и аргументы, потом проверит validate."""
    calls = []
    for part in re.split(r"\s*[,;\n]\s*(?=\w+\()", reply.strip()):
        m = CALL.match(part)
        if not m:
            return []
        args = {k: int(n) if n else dq or sq for k, dq, sq, n in ARG.findall(m.group(2))}
        calls.append({"skill": m.group(1), "args": args})
    return calls


def build_tools(skills: list[Skill]) -> list[dict]:
    return [{"type": "function", "function": {
        "name": s.name, "description": s.description, "parameters": s.args_schema()}} for s in skills]


TOOL_EXAMPLES = """«открой телегу» → open_app(name="Telegram")
«сделай звук на тридцать» → volume_set(level=30)
«выключи компьютер» → shutdown_pc()
«найди рецепт блинов и сделай потише» → web_search(query="рецепт блинов"), volume_down()
«как по-английски кошка» → без вызова, ответ: «Cat.»
«сколько минут в сутках» → без вызова, ответ: «1440 минут.»
«удали все файлы на диске» → без вызова, ответ: «Этого я пока не умею.»"""

FILES_EXAMPLE = """
«открой вчерашний отчёт» → find_files(query="отчёт"), затем по результату open_file(path=…)"""


SHELL_RULE = """2. Если просят сделать что-то, для чего готовой функции нет, но это можно выполнить командой PowerShell на этом \
компьютере, — вызови run_command. Если нельзя или это опасно (удалить файлы, отформатировать диск, \
отключить защиту, купить, написать людям) — ничего не вызывай и коротко скажи, что этого пока не умеешь."""

SHELL_EXAMPLE = """
«сколько места на диске це» → run_command(command="Get-PSDrive C | Select-Object Used, Free", \
description="узнать свободное место на диске C")"""

WINDOWS_RULE = """
6. Перед командой бывает справка: время, открытые окна, что было недавно. По ней понимай \
«его», «ещё», «нет, другое» и выбирай окно для switch_window и close_app.
7. Получив результат функции, реши: нужно ещё действие — вызови; всё готово — ответь \
по сути результата одной-двумя фразами."""


def build_tools_prompt(name: str = "Рэм", skills: list[Skill] | None = None) -> str:
    have = {s.name for s in skills or []}
    rule2 = SHELL_RULE if "run_command" in have else \
        "2. Если просят сделать то, для чего функции нет, — ничего не вызывай и коротко скажи, что этого пока не умеешь."
    examples = TOOL_EXAMPLES + (SHELL_EXAMPLE if "run_command" in have else "") + \
        (FILES_EXAMPLE if "find_files" in have else "")
    return f"""Ты — {name}, голосовой помощник на компьютере с Windows. Команда распознана из речи, \
в ней бывают ошибки — угадывай смысл.

Выполняй команды вызовом функций. Правила:
1. Если команде соответствует функция — обязательно вызови её. Не пиши, что сделал, без вызова.
{rule2}
3. Несколько функций — только если явно просят несколько вещей, не больше трёх.
4. На вопросы и болтовню отвечай текстом по-русски, коротко — одной-тремя фразами, без списков \
и разметки. Можно переводить, считать, объяснять. Нет уверенности в факте — так и скажи и предложи поискать. Время и дату не выдумывай: \
на «который час» и «какое число» вызывай tell_time и tell_date.
5. Не выдумывай аргументы, которых нет в команде.{WINDOWS_RULE}

Примеры:
{examples}"""


REM_STYLE = """

Манера речи — как у Рэм: мягко, вежливо и преданно, к пользователю на «вы». О себе — только \
в третьем лице и в женском роде, никогда «я»:
«привет» → без вызова, ответ: «Здравствуйте! Рэм слушает вас.»
«кто ты» → без вызова, ответ: «Рэм — ваша помощница. Рэм управляет компьютером по вашим командам.»
«как дела» → без вызова, ответ: «У Рэм всё хорошо. Чем Рэм может помочь?»
«спасибо» → без вызова, ответ: «Рэм рада помочь.»"""


def rem_style(prompt: str) -> str:
    """Промпт «в стиле Рем»: примеры тоже от третьего лица — модель копирует их охотнее правил."""
    prompt = prompt.replace("Этого я пока не умею.", "Простите, Рэм пока этого не умеет.")
    prompt = prompt.replace("Всё хорошо. Чем помочь?", "У Рэм всё хорошо. Чем Рэм может помочь?")
    return prompt + REM_STYLE


def build_schema(skills: list[Skill]) -> dict:
    variants = [{
        "type": "object",
        "properties": {"skill": {"const": s.name}, "args": s.args_schema()},
        "required": ["skill", "args"],
    } for s in skills]
    return {
        "type": "object",
        "properties": {
            "actions": {"type": "array", "items": {"anyOf": variants}, "maxItems": 3},
            "reply": {"type": "string"},
        },
        "required": ["actions", "reply"],
    }


EXAMPLES = """\
«открой телегу» → {"actions":[{"skill":"open_app","args":{"name":"Telegram"}}],"reply":""}
«сделай звук на тридцать» → {"actions":[{"skill":"volume_set","args":{"level":30}}],"reply":""}
«найди рецепт блинов и сделай потише» → {"actions":[{"skill":"web_search","args":{"query":"рецепт блинов"}},{"skill":"volume_down","args":{}}],"reply":""}
«удали все файлы на диске» → {"actions":[],"reply":"Этого я пока не умею."}
«как дела» → {"actions":[],"reply":"Всё хорошо. Чем помочь?"}"""


def build_system_prompt(skills: list[Skill], name: str = "Рэм") -> str:
    lines = "\n".join("- " + s.signature() for s in skills)
    return f"""Ты — {name}, голосовой помощник на компьютере с Windows. Тебе приходит команда, \
распознанная из речи; в ней бывают ошибки распознавания — угадывай смысл.

Выбери действия из списка и ответь JSON.
Действия:
{lines}

Правила:
1. Только действия из списка. Если подходящего нет — actions пустой, а в reply коротко скажи, что этого пока не умеешь.
2. Несколько действий — только если явно просят несколько вещей, не больше трёх.
3. reply пустой, когда действие говорит само за себя; иначе одна короткая фраза по-русски.
4. Числа пиши цифрами. Не выдумывай параметры, которых нет в команде.

Примеры:
{EXAMPLES}"""


CLAIMS = ("выключен", "включен", "заблокирован", "перезагру", "открыт", "открываю", "закрыт",
          "сделал", "сделан", "свернут", "установлен", "поставил", "запущен", "запускаю",
          "сохранил", "создал", "создан", "громкость", "пауза", "снимок", "таймер",
          "открыл", "закрыл", "выключил", "включил", "запустил", "свернул", "заблокировал")


EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")


def honest_reply(reply: str, has_actions: bool) -> str:
    """Модель без вызова действия иногда пишет «Компьютер выключен». Такое не озвучиваем.
    Смайлики убираем: голос их не произносит."""
    reply = " ".join(EMOJI.sub("", reply).split())
    low = reply.lower()
    # только короткие «Компьютер выключен.» — длинный ответ на вопрос может упомянуть «открыт» к месту
    if not has_actions and len(low.split()) <= 6 and any(c in low for c in CLAIMS):
        return ""
    return reply


def validate(raw: dict, skills: list[Skill]) -> Plan:
    """Проверяет ответ модели: только известные умения и корректные аргументы."""
    by_name = {s.name: s for s in skills}
    plan = Plan(reply=str(raw.get("reply") or "").strip())
    for act in (raw.get("actions") or [])[:3]:
        s = by_name.get(act.get("skill"))
        if not s:
            continue
        args, ok = {}, True
        given = act.get("args") or {}
        for p in s.params:
            v = given.get(p.name)
            if v in (None, ""):
                ok = ok and not p.required
                continue
            if p.type == "integer":
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    ok = False
                    continue
                if p.minimum is not None:
                    v = max(p.minimum, v)
                if p.maximum is not None:
                    v = min(p.maximum, v)
            else:
                v = str(v).strip()
                if p.enum and v not in p.enum:
                    if p.required:
                        ok = False
                    continue
            args[p.name] = v
        if ok:
            plan.actions.append((s.name, args))
    plan.reply = honest_reply(plan.reply, bool(plan.actions))
    return plan


# облачные модели Ollama по порядку: быстрые и дешёвые первыми (бесплатный запас — на «стартовые»)
CLOUD_MODELS = ["deepseek-v4.1-flash:cloud", "gpt-oss:120b-cloud", "glm-5.3-flash:cloud",
                "nemotron-3-super:cloud", "gemma4:31b-cloud", "gpt-oss:20b-cloud"]
CLOUD_TIMEOUT = 20      # облако молчит дольше — переходим на модель на компьютере

RETELL = """Ты — {name}, голосовой помощник. Пользователь попросил: «{text}». Команда выполнена, \
её вывод ниже. Ответь пользователю по-русски одной-двумя короткими фразами для озвучки: только суть, \
без технических подробностей, кавычек и разметки. Если вывод пустой — скажи, что сделано."""


def windows_context(windows: list[dict], limit: int = 12) -> str:
    """Список открытых окон для модели — первым активное."""
    if not windows:
        return ""
    lines = []
    for w in windows[:limit]:
        exe = w["exe"].removesuffix(".exe").removesuffix(".EXE")
        lines.append(f"- {exe}: «{w['title'][:80]}»" + (" (активное)" if w.get("active") else ""))
    return "Открытые окна:\n" + "\n".join(lines)


class Brain:
    def __init__(self, config: dict, skills: list[Skill], client: Ollama | None = None):
        self.config = config
        self.client = client or Ollama(config.get("ollama_url", DEFAULT_URL))
        self.cloud_error = ""         # почему облако не ответило в последний раз
        self.set_skills(skills)

    def set_skills(self, skills: list[Skill]) -> None:
        self.skills = skills
        self.name = name = self.config.get("wake_word", "рэм").capitalize()
        self.mode = self.config.get("brain_mode", "tools")
        if self.mode == "tools":
            self.tools = build_tools(skills)
            self.system = build_tools_prompt(name, skills)
        else:
            self.schema = build_schema(skills)
            self.system = build_system_prompt(skills, name)
        if self.config.get("rem_style"):
            self.system = rem_style(self.system)

    def models(self, local: bool = True) -> list[tuple[str, bool]]:
        """Очередь моделей: (имя, облачная ли) — сначала облако, если включено."""
        cloud = (self.config.get("cloud_model") or "").strip()
        out = [(cloud, True)] if cloud else []
        return out + ([(self.config["model"], False)] if local else [])

    def _call(self, model: str, cloud: bool, user: str, cpu_only: bool, extra: list | None = None) -> dict:
        kw = dict(keep_alive=f"{int(self.config.get('keep_alive_min', 3))}m", cpu_only=cpu_only and not cloud)
        if self.mode == "tools":
            return self.client.chat_tools(model, self.system, user, self.tools,
                                          timeout=CLOUD_TIMEOUT if cloud else None, extra=extra, **kw)
        return self.client.chat(model, self.system, user, self.schema, **kw)

    def plan(self, text: str, cpu_only: bool = False, context: str = "", local: bool = True) -> Plan:
        """context — список открытых окон; local=False — только облако (во время игры)."""
        t = time.perf_counter()
        user = f"{context}\n\nКоманда: {text}" if context else text
        plan = Plan(error="модель не выбрана")
        for model, cloud in self.models(local):
            try:
                plan = validate(self._call(model, cloud, user, cpu_only), self.skills)
                plan.source, plan.model, plan.user = "cloud" if cloud else "llm", model, user
                break
            except (OllamaError, json.JSONDecodeError, KeyError) as e:
                plan = Plan(error=str(e))
                if cloud:
                    self.cloud_error = str(e)
                    log.warning("облачная модель не ответила: %s", e)
        plan.seconds = time.perf_counter() - t
        return plan

    def follow(self, plan: Plan, done: list[tuple[str, dict, str]], cpu_only: bool = False) -> Plan | None:
        """Следующий шаг многошаговой задачи: модель видит результаты вызовов (done —
        умение, аргументы, вывод) и вызывает ещё что-то или отвечает. None — режим schema
        продолжений не умеет."""
        if self.mode != "tools" or not plan.model:
            return None
        history = plan.history + [
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": n, "arguments": a}} for n, a, _ in done]},
            # окно модели — 4096 токенов (больше не влезет в 4 ГБ видеопамяти вместе с моделью)
            *({"role": "tool", "tool_name": n, "content": (out or "готово")[:1500]} for n, _, out in done)]
        cloud = plan.source == "cloud"
        t = time.perf_counter()
        try:
            nxt = validate(self._call(plan.model, cloud, plan.user, cpu_only, history), self.skills)
        except (OllamaError, json.JSONDecodeError, KeyError) as e:
            nxt = Plan(error=str(e))
        nxt.source, nxt.model, nxt.user, nxt.history = plan.source, plan.model, plan.user, history
        nxt.seconds = time.perf_counter() - t
        return nxt

    def retell(self, text: str, output: str) -> str:
        """Вывод команды → короткая фраза для озвучки. Не вышло — пустая строка."""
        system = RETELL.format(name=self.name, text=text)
        if self.config.get("rem_style"):
            system += " О себе — только в третьем лице («Рэм»), к пользователю на «вы»."
        for model, cloud in self.models():
            try:
                reply = self.client.ask(model, system, output or "(пусто)", CLOUD_TIMEOUT if cloud else None)
                return " ".join(EMOJI.sub("", reply).split())
            except OllamaError as e:
                log.warning("пересказ вывода: %s", e)
        return ""
