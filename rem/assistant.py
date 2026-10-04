"""Ядро: команда → план (быстрый путь или модель) → подтверждение → выполнение → ответ."""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import winapi
from .brain import Brain, Ollama, Plan
from .config import app_dir
from .fastpath import FastPath
from .skills import active_skills
from .skills.apps import AppIndex
from .timers import Timers

log = logging.getLogger("rem.assistant")


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
        """Загрузить модель, пока человек договаривает команду (после «голого» слова)."""
        if self.game_mode:
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
        if self.game_mode:
            mode = self.config.get("game_mode", "fast_only")
            if mode == "fast_only":
                return Plan(reply="Во время игры я выполняю только простые команды.", source="game")
            return self.brain.plan(text, cpu_only=True)
        plan = self.brain.plan(text)
        if not plan.error and not self._gpu_checked:
            self._gpu_checked = True
            threading.Thread(target=self._check_gpu, daemon=True).start()
        return plan

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
        """Выполнить команду. dry_run — только показать план."""
        self.status_cb("thinking")
        t0 = time.perf_counter()
        plan = self.plan(text)
        replies = []
        if plan.error:
            log.warning("модель: %s", plan.error)
            replies.append("Не могу связаться с моделью. Проверь, запущена ли Ollama."
                           if "недоступна" in plan.error else "Не удалось понять команду.")
        elif not plan.actions and not plan.reply:
            replies.append("Команда не распознана.")
        if plan.reply:
            replies.append(plan.reply)

        results = []
        for name, args in plan.actions:
            s = self.by_name.get(name)
            if not s:
                continue
            if dry_run:
                results.append((name, args, "пропущено (проверка)"))
                continue
            if s.confirm and not self._confirm(s.title):
                replies.append("Отменено.")
                results.append((name, args, "отменено"))
                continue
            try:
                out = s.handler(self.ctx, **args)
                if out:
                    replies.append(out)
                results.append((name, args, "ок"))
            except winapi.NotOnWindows:
                results.append((name, args, "только в Windows"))
            except Exception as e:
                log.exception("умение %s упало", name)
                replies.append(f"Не получилось: {s.title.lower()}.")
                results.append((name, args, f"ошибка: {e}"))

        self._write_journal(text, plan, results, time.perf_counter() - t0)
        if not dry_run:
            if replies:
                self.say(" ".join(dict.fromkeys(replies)))
            elif plan.actions and self.config.get("rem_style") and self.voice:
                self.say("Сделано.")
            elif plan.actions:
                self._sound("done")
            else:
                self._sound("error")
            if self.voice:
                self.voice.wait()
        self.status_cb("game" if self.game_mode else "idle")
        return plan

    def _confirm(self, title: str) -> bool:
        if not self.listener or not self.voice:
            return False                       # без микрофона опасное не выполняем
        self.voice.say(f"{title}? Скажи «да» или «нет».")
        self.voice.wait()
        self.listener.mic.flush()
        return self.listener.hear_yes_no()

    def _write_journal(self, text: str, plan: Plan, results: list, seconds: float) -> None:
        rec = {"time": dt.datetime.now().isoformat(timespec="seconds"), "text": text,
               "source": plan.source, "actions": results, "reply": plan.reply,
               "model_s": round(plan.seconds, 2), "total_s": round(seconds, 2)}
        if plan.error:
            rec["error"] = plan.error
        try:
            with open(self.journal, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
        log.info("команда «%s» → %s %s за %.2f с", text, plan.source, results, seconds)
