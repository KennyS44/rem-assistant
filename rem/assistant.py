"""Ядро: команда → план (быстрый путь или модель) → подтверждение → выполнение → ответ."""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from . import winapi
from .brain import Brain, Ollama, Plan, windows_context
from .config import app_dir
from .fastpath import FastPath
from .skills import active_skills
from .skills.apps import AppIndex
from .skills.system import MONTHS, WEEKDAYS
from .timers import Timers

log = logging.getLogger("rem.assistant")

MAX_STEPS = 4         # многошаговая задача: не больше 4 обращений к модели
RECENT_S = 180        # «закрой его» понимаем по командам за последние 3 минуты


@dataclass
class Context:
    """То, что видят обработчики умений."""
    config: dict
    apps: AppIndex
    timers: Timers
    say: Callable[[str], None]


class Assistant:
    def __init__(self, config: dict, voice=None, sounds=None, listener=None, client: Ollama | None = None,
                 apps: AppIndex | None = None):
        self.config = config
        self.voice = voice
        self.sounds = sounds
        self.listener = listener
        self.client = client or Ollama(config.get("ollama_url"))
        self.apps = apps or AppIndex()
        self.timers = Timers(self._timer_done)
        self.ctx = Context(config, self.apps, self.timers, self.say)
        self.game_mode = False
        self.status_cb: Callable[[str], None] = lambda s: None
        self.notify_cb: Callable[[str], None] = lambda s: None
        self._gpu_checked = False
        self._cloud_warned = False
        self.recent: deque = deque(maxlen=4)       # (когда, команда, результаты, ответ) — память разговора
        self.journal = app_dir() / "actions.log"
        self.reload()

    # ——— настройка ———

    def reload(self) -> None:
        """Перечитать умения после изменения настроек."""
        self.skills = active_skills(self.config)
        self.by_name = {s.name: s for s in self.skills}
        self.fast = FastPath(self.skills, self.apps)
        self.brain = Brain(self.config, self.skills, self.client)

    def say(self, text: str) -> None:
        if self.voice and self.config.get("speak_replies", True):
            self.voice.say(text)
        else:
            log.info("ответ: %s", text)

    def phrase(self, plain: str, rem: str) -> str:
        """Служебная фраза: обычная или «в стиле Рем» (о себе в третьем лице, на «вы»)."""
        return rem if self.config.get("rem_style") else plain

    def _sound(self, name: str) -> None:
        if self.sounds:
            self.sounds.play(name)

    def _timer_done(self, message: str) -> None:
        self._sound("timer")
        time.sleep(1.2)
        self.say(message)

    # ——— игровой режим ———

    def watch_game_mode(self, stop: threading.Event, period: float = 3.0) -> None:
        """Полноэкранное окно → видеокарту не трогаем, модель выгружаем."""
        while not stop.wait(period):
            mode = self.config.get("game_mode", "fast_only")
            full = mode != "off" and winapi.foreground_is_fullscreen()
            if full and not self.game_mode:
                self.game_mode = True
                log.info("игровой режим: включён")
                self.status_cb("game")
                threading.Thread(target=self._unload, daemon=True).start()
            elif not full and self.game_mode:
                self.game_mode = False
                log.info("игровой режим: выключен")
                self.status_cb("idle")

    def _unload(self) -> None:
        try:
            if any(m.get("name", "").startswith(self.config["model"].split(":")[0])
                   for m in self.client.loaded()):
                self.client.unload(self.config["model"])
        except Exception as e:
            log.debug("выгрузка модели: %s", e)

    def prewarm(self) -> None:
        """Загрузить модель, пока человек договаривает команду (после «голого» слова).
        С облачной моделью видеокарту не занимаем: локальная нужна, только если облако не ответит."""
        if self.game_mode or self.config.get("cloud_model"):
            return

        def go():
            try:
                self.client.warm(self.config["model"], f"{int(self.config.get('keep_alive_min', 3))}m")
            except Exception as e:
                log.debug("прогрев: %s", e)
        threading.Thread(target=go, daemon=True).start()

    # ——— главное ———

    def plan(self, text: str) -> Plan:
        fast = self.fast.match(text)
        if fast:
            return fast
        context = self.context()
        if self.game_mode:
            # облако видеокарту не трогает — во время игры им можно пользоваться как обычно
            mode = self.config.get("game_mode", "fast_only")
            plan = self.brain.plan(text, cpu_only=True, context=context, local=mode == "cpu")
            self._warn_cloud(plan)
            if plan.error and mode == "fast_only":
                return Plan(reply="Во время игры я выполняю только простые команды.", source="game")
            return plan
        plan = self.brain.plan(text, context=context)
        self._warn_cloud(plan)
        if plan.source == "llm" and not plan.error and not self._gpu_checked:
            self._gpu_checked = True
            threading.Thread(target=self._check_gpu, daemon=True).start()
        return plan

    def context(self) -> str:
        """Справка для модели: время, открытые окна, последние команды (за RECENT_S секунд)."""
        now = dt.datetime.now()
        parts = [f"Сейчас: {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} {now.year}, {now:%H:%M}."]
        try:
            parts.append(windows_context(winapi.open_windows()))
        except Exception as e:
            log.debug("список окон: %s", e)
        fresh = [r for r in self.recent if time.monotonic() - r[0] < RECENT_S]
        if fresh:
            lines = []
            for _, said, results, spoken in fresh:
                acts = ", ".join(f"{n}({', '.join(f'{k}={v!r}' for k, v in a.items())}) — {st}"
                                 for n, a, st in results)
                lines.append(f"- «{said}» → {acts or 'без действий'}" + (f"; ответ: «{spoken[:150]}»" if spoken else ""))
            parts.append("Недавно (сначала старое):\n" + "\n".join(lines))
        return "\n\n".join(p for p in parts if p)

    def _warn_cloud(self, plan: Plan) -> None:
        """Один раз за запуск: облако не ответило — говорим почему (дальше молча работаем локально)."""
        err = self.brain.cloud_error
        if not self.config.get("cloud_model") or plan.source == "cloud" or not err or self._cloud_warned:
            return
        self._cloud_warned = True
        if "401" in err or "unauthorized" in err.lower():
            why = "Ollama не вошла в аккаунт — нажмите «Подключить» в настройках."
        elif "недоступна" in err:
            why = "нет связи с облаком Ollama."
        else:
            why = "облако ответило ошибкой (возможно, закончился бесплатный лимит). Подробности в rem.log."
        self.notify_cb("Облачная модель не ответила, работаю на модели на компьютере: " + why)

    def _check_gpu(self) -> None:
        """Один раз: модель на видеокарте? Если нет — чаще всего старый драйвер NVIDIA."""
        try:
            loaded = [m for m in self.client.loaded() if m.get("name") == self.config["model"]]
        except Exception:
            return
        if loaded and not loaded[0].get("size_vram"):
            log.warning("модель работает на процессоре")
            self.notify_cb("Модель работает на процессоре, а не на видеокарте — ответы будут медленнее. "
                           "Для GTX 10xx нужен драйвер NVIDIA версии 570 или новее.")

    def handle(self, text: str, dry_run: bool = False) -> Plan:
        """Выполнить команду. dry_run — только показать план.
        Если умение вернуло данные (поиск файлов, вывод команды), модель видит их и решает,
        что дальше: ещё действие или ответ. Не больше MAX_STEPS шагов."""
        self.status_cb("thinking")
        t0 = time.perf_counter()
        plan = first = self.plan(text)
        replies, results = [], []
        if plan.error:
            log.warning("модель: %s", plan.error)
            replies.append("Не могу связаться с моделью. Проверь, запущена ли Ollama."
                           if "недоступна" in plan.error else "Не удалось понять команду.")
        elif not plan.actions and not plan.reply:
            replies.append(self.phrase("Команда не распознана.", "Простите, Рэм не расслышала."))

        for _ in range(MAX_STEPS):
            if plan.reply:
                replies.append(plan.reply)
            done, more = self._run(plan, dry_run, replies, results)
            if not more:
                break
            nxt = self.brain.follow(plan, done, cpu_only=self.game_mode)
            if nxt is None or nxt.error:              # продолжение не вышло — хотя бы пересказ
                if nxt is not None:
                    log.warning("следующий шаг: %s", nxt.error)
                for name, _, out in done:
                    if self.by_name[name].retell:
                        replies.append(self.brain.retell(text, out) or self.phrase("Готово.", "Рэм всё сделала."))
                break
            plan = nxt
            if not plan.actions and not plan.reply:
                replies.append(self.phrase("Готово.", "Рэм всё сделала."))

        spoken = " ".join(dict.fromkeys(replies))
        self._write_journal(text, first, results, time.perf_counter() - t0, spoken)
        if not dry_run:
            self.recent.append((time.monotonic(), text, results, spoken))
            if spoken:
                self.say(spoken)
            elif results and self.config.get("rem_style") and self.voice:
                self.say("Рэм всё сделала.")
            elif results:
                self._sound("done")
            else:
                self._sound("error")
            if self.voice:
                self.voice.wait()
        self.status_cb("game" if self.game_mode else "idle")
        return first

    def _run(self, plan: Plan, dry_run: bool, replies: list, results: list) -> tuple[list, bool]:
        """Выполнить действия плана. → (что сделано для модели: умение, аргументы, вывод;
        нужен ли следующий шаг — умение вернуло данные)."""
        done, more = [], False
        for name, args in plan.actions:
            s = self.by_name.get(name)
            if not s:
                continue
            if dry_run:
                results.append((name, args, "пропущено (проверка)"))
                continue
            if s.name == "run_command":
                self.notify_cb("Команда: " + args.get("command", ""))
            question = args.get("description") or s.title if s.name == "run_command" else s.title
            if s.confirm and not self._confirm(question[:1].upper() + question[1:]):
                replies.append(self.phrase("Отменено.", "Хорошо, Рэм не будет."))
                results.append((name, args, "отменено"))
                return done, False                     # «нет» — дальше не продолжаем
            try:
                out = s.handler(self.ctx, **args)
                if out and not s.retell:
                    replies.append(out)
                results.append((name, args, "ок"))
                done.append((name, args, out or ""))
                more = more or s.retell
            except winapi.NotOnWindows:
                results.append((name, args, "только в Windows"))
            except Exception as e:
                log.exception("умение %s упало", name)
                results.append((name, args, f"ошибка: {e}"))
                if s.retell:                           # модель увидит ошибку и попробует иначе или объяснит
                    done.append((name, args, f"ошибка: {e}"))
                    more = True
                else:
                    replies.append(self.phrase("Не получилось: ", "Простите, у Рэм не получилось: ")
                                   + f"{s.title.lower()}.")
        return done, more

    def _confirm(self, title: str) -> bool:
        if not self.listener or not self.voice:
            return False                       # без микрофона опасное не выполняем
        self.voice.say(title + self.phrase("? Скажи «да» или «нет».", "? Скажите «да» или «нет»."))
        self.voice.wait()
        self.listener.mic.flush()
        return self.listener.hear_yes_no()

    def _write_journal(self, text: str, plan: Plan, results: list, seconds: float, spoken: str = "") -> None:
        rec = {"time": dt.datetime.now().isoformat(timespec="seconds"), "text": text,
               "source": plan.source, "actions": results, "reply": spoken or plan.reply,
               "model_s": round(plan.seconds, 2), "total_s": round(seconds, 2)}
        if plan.error:
            rec["error"] = plan.error
        try:
            with open(self.journal, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
        log.info("команда «%s» → %s %s за %.2f с", text, plan.source, results, seconds)
