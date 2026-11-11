"""Ядро: команда → план (быстрый путь или модель) → подтверждение → выполнение → ответ."""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable

from . import winapi
from .brain import CLICK_HINT, Brain, Ollama, Plan, claims_done, windows_context
from .config import app_dir
from .fastpath import FastPath, clean
from .phrasebook import FORGET_ALL, UNDO, Phrasebook, learnable
from .skills import active_skills
from .skills.base import Cancelled, NotHere
from .skills import screen
from .skills.apps import AppIndex
from .skills.system import MONTHS, WEEKDAYS
from .timers import Timers

log = logging.getLogger("rem.assistant")

MAX_STEPS = 8         # многошаговая задача: не больше 8 обращений к модели (мастер установки — 5–6 окон)
RECENT_S = 180        # «закрой его» понимаем по командам за последние 3 минуты


@dataclass
class Context:
    """То, что видят обработчики умений."""
    config: dict
    apps: AppIndex
    timers: Timers
    say: Callable[[str], None]
    confirm: Callable[[str], bool] = lambda question: False     # «да/нет» голосом — опасная кнопка
    dictate: Callable[[], str] = lambda: "Диктовка работает только с микрофоном."
    reminders: object = None                                    # reminders.Reminders
    selection: Callable[[], tuple] = lambda: ("", "")           # выделенный текст и откуда он
    ask: Callable[..., object] = lambda system, text, tokens=200: ""   # модель: ответ текстом
    copy: Callable[[str], None] = lambda text: None             # положить в буфер обмена
    show: Callable[[str], None] = lambda text: None             # показать, не читая вслух


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
        from .reminders import Reminders
        self.reminders = Reminders(app_dir() / "reminders.json", self._reminder_due)
        self.ctx = Context(config, self.apps, self.timers, self.say, lambda q: self._confirm(q),
                           lambda: self.dictate(), self.reminders, self._selection, self._ask_text,
                           self._copy, lambda text: self.notify_cb(text))
        self.heard_at = 0.0                        # когда к Рэм обращались — будильник замолкает
        self.game_mode = False
        self.status_cb: Callable[[str], None] = lambda s: None
        self.notify_cb: Callable[[str], None] = lambda s: None
        self._gpu_checked = False
        self._cloud_warned = False
        self.recent: deque = deque(maxlen=4)       # (когда, команда, результаты, ответ) — память разговора
        self._failed = ""
        self.journal = app_dir() / "actions.log"
        self.book = Phrasebook(app_dir() / "phrasebook.json")
        self.last: tuple[str, str] = ("", "")     # прошлая команда и откуда план — для «не то»
        self.reload()

    # ——— настройка ———

    def reload(self) -> None:
        """Перечитать умения после изменения настроек."""
        self.skills = active_skills(self.config)
        self.by_name = {s.name: s for s in self.skills}
        self.fast = FastPath(self.skills, self.apps)
        self.brain = Brain(self.config, [s for s in self.skills if s.model], self.client)

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

    def plan(self, text: str, fast_ok: bool = True) -> Plan:
        fast = self.fast.match(text) if fast_ok else None
        if fast:
            return fast
        if fast_ok and self.config.get("learn_phrases", True):
            learned = self.book.get(text)
            if learned and all(n in self.by_name for n, _ in learned):
                return Plan(actions=learned, source="learned")
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
        if "click_button" in self.by_name:
            try:
                dialog = screen.active_dialog(self.ctx)          # «подтверди» — видно, что за «Да»
                if dialog:
                    parts.append(dialog + "\n" + CLICK_HINT)
            except Exception as e:
                log.debug("активное окно: %s", e)
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
        self.heard_at = time.monotonic()
        special = self._special(text)
        if special is not None:
            if not dry_run:
                self.say(special)
                self._wait_voice()
            return Plan(reply=special, source="fast")
        self.status_cb("thinking")
        t0 = time.perf_counter()
        plan = first = self.plan(text)
        steps = 0
        replies, results = [], []
        if plan.error:
            log.warning("модель: %s", plan.error)
            replies.append("Не могу связаться с моделью. Проверь, запущена ли Ollama."
                           if "недоступна" in plan.error else "Не удалось понять команду.")
        elif not plan.actions and not plan.reply:
            replies.append(self.phrase("Команда не распознана.", "Простите, Рэм не расслышала."))

        last = None
        for _ in range(MAX_STEPS):
            if plan.reply:
                replies.append(plan.reply)
            steps += 1
            try:
                done, more = self._run(plan, dry_run, replies, results)
            except NotHere:                            # быстрый путь не подошёл — спрашиваем модель
                if plan.source == "learned":
                    self.book.forget(text)
                plan = first = self.plan(text, fast_ok=False)
                steps = 0                              # считаем шаги заново — уже ответа модели
                if plan.error:
                    replies.append("Не удалось понять команду.")
                    break
                continue
            if not more:
                break
            if done == last:                          # то же действие, тот же результат — топчемся на месте
                replies.append(self.phrase("Не получилось.", "Простите, у Рэм не получилось."))
                break
            last = done
            nxt = self.brain.follow(plan, done, cpu_only=self.game_mode)
            if nxt is None or nxt.error:              # продолжение не вышло — хотя бы пересказ
                if nxt is not None:
                    log.warning("следующий шаг: %s", nxt.error)
                for name, _, out in done:
                    if self.by_name[name].retell:
                        replies.append(self.brain.retell(text, out) or self.phrase("Готово.", "Рэм всё сделала."))
                break
            plan = nxt
            if self._failed and not plan.actions and (not plan.reply or claims_done(plan.reply)):
                plan = replace(plan, reply=self._failed)   # после неудачи «готово» не говорим — говорим, что вышло
            if not plan.actions and not plan.reply:
                replies.append(self.phrase("Готово.", "Рэм всё сделала."))

        spoken = " ".join(dict.fromkeys(replies))
        self._write_journal(text, first, results, time.perf_counter() - t0, spoken)
        if not dry_run:
            self._learn(text, first, results, steps)
            self.recent.append((time.monotonic(), text, results, spoken))
            if spoken:
                self.say(spoken)
            elif results and self.config.get("rem_style") and self.voice:
                self.say("Рэм всё сделала.")
            elif results:
                self._sound("done")
            else:
                self._sound("error")
            self._wait_voice()
        self.status_cb("game" if self.game_mode else "idle")
        return first

    # ——— выученные команды ———

    def _learn(self, text: str, first: Plan, results: list, steps: int) -> None:
        """Модель поняла команду с одного раза и всё получилось — запомнить (phrasebook.py)."""
        self.last = (text, first.source)
        ok = results and all(st == "ок" for _, _, st in results) and len(results) == len(first.actions)
        if first.source == "learned" and not ok:
            self.book.forget(text)                 # выученное перестало работать — пусть решает модель
        elif (first.source in ("llm", "cloud") and not first.error and ok and steps == 1
              and self.config.get("learn_phrases", True) and learnable(text, first.actions, self.by_name)):
            self.book.learn(text, first.actions)

    def _special(self, text: str) -> str | None:
        """«Не то» и «забудь выученные команды» — про сами выученные команды, до всего остального."""
        key = clean(text)
        if key in FORGET_ALL:
            n = self.book.clear()
            return self.phrase(f"Забыла выученные команды: {n}.", f"Рэм забыла выученные команды: {n}.")
        if key in UNDO:
            said, source = self.last
            self.last = ("", "")
            if said and self.book.forget(said):
                return self.phrase(f"Поняла, забыла, как выполнять «{said}». В следующий раз спрошу модель.",
                                   f"Простите. Рэм забыла, как выполнять «{said}».")
            if said:
                return self.phrase("Поняла. Скажите по-другому — попробую ещё раз.",
                                   "Простите. Скажите по-другому — Рэм попробует ещё раз.")
        return None

    # ——— перебивание ———

    def _wait_voice(self) -> None:
        """Дождаться конца ответа. Если перебили («Рэм, …» поверх голоса) — выполнить новое."""
        if not self.voice:
            return
        got = self.listener.wait_speaking() if self.listener and self.listener.can_barge() else None
        self.voice.wait()
        if got is not None:
            self.after_barge(got)

    def after_barge(self, said: str) -> None:
        from .listen import is_stop
        self.heard_at = time.monotonic()
        if is_stop(said):
            self.status_cb("game" if self.game_mode else "idle")
        elif said:
            self.handle(said)
        else:
            self.wake_only()

    def wake_only(self) -> None:
        """Сказали только «Рэм» — ждём команду."""
        self.status_cb("listening")
        if self.config.get("rem_style") and self.voice:
            self.voice.say("Рэм слушает.")
            self.voice.wait()
            if self.listener:
                self.listener.mic.flush()
        else:
            self._sound("wake")
        self.prewarm()                          # пока человек говорит — грузим модель
        command = self.listener.hear_command() if self.listener else ""
        if command:
            self.handle(command)
        else:
            self.status_cb("game" if self.game_mode else "idle")

    # ——— напоминания и будильник ———

    def _reminder_due(self, item: dict, missed: bool) -> None:
        from .reminders import due_phrase, say_when
        if missed:
            at = dt.datetime.fromisoformat(item["at"])
            what = "будильник" if item.get("kind") == "alarm" else (item.get("text") or "напоминание")
            self.say(f"Пока Рэм не работала, прошло: {say_when(at)} — {what}.")
            return
        if item.get("kind") == "alarm":
            threading.Thread(target=self._ring, args=(item,), name="alarm", daemon=True).start()
            return
        self._sound("timer")
        time.sleep(1.2)
        self.say(due_phrase(item))

    def _ring(self, item: dict, times: int = 3, gap: float = 60.0) -> None:
        """Будильник: звонок и фраза; повтор через минуту, пока не обратятся к Рэм («Рэм, хватит»)."""
        from .reminders import due_phrase
        start = time.monotonic()
        for i in range(times):
            if self.heard_at > start:
                return
            for _ in range(3):
                self._sound("timer")
                time.sleep(1.4)
            if self.heard_at > start:
                return
            self.say(due_phrase(item))
            if i + 1 < times:
                deadline = time.monotonic() + gap
                while time.monotonic() < deadline and self.heard_at <= start:
                    time.sleep(0.5)

    # ——— выделенный текст ———

    def _selection(self) -> tuple[str, str]:
        from . import selection
        return selection.grab()

    def _copy(self, text: str) -> None:
        if not winapi.IS_WINDOWS:
            return
        from . import selection
        try:
            selection.set_clipboard_text(text)
        except Exception as e:
            log.warning("в буфер не положила: %s", e)

    def _ask_text(self, system: str, text: str, tokens: int = 200):
        """Модель отвечает текстом. None — во время игры модель не трогаем (настройка)."""
        cloud = bool(self.config.get("cloud_model"))
        if self.game_mode and self.config.get("game_mode", "fast_only") == "fast_only" and not cloud:
            return None
        self.status_cb("thinking")
        game = self.game_mode and self.config.get("game_mode", "fast_only")
        return self.brain.ask_text(system, text, tokens, local=game != "fast_only", cpu_only=game == "cpu")

    # ——— диктовка ———

    def dictate(self) -> str:
        """Печатать сказанное в активное окно, пока не скажут «хватит» (dictation.py)."""
        from .dictation import IDLE_S, Dictation
        from .listen import find_wake, record_phrase
        lst = self.listener
        if lst is None:
            return "Диктовка работает только с микрофоном."
        if not winapi.IS_WINDOWS:
            raise winapi.NotOnWindows()
        if self.voice:
            self.voice.say(self.phrase("Пишу. Скажите «хватит», чтобы закончить.",
                                       "Рэм записывает. Скажите «хватит», чтобы закончить."))
            self.voice.wait()
        lst.mic.flush()
        self.status_cb("dictating")
        d = Dictation(winapi.type_unicode,
                      lambda: winapi.press(winapi.VK["shift"], winapi.VK["enter"]),
                      lambda n: [winapi.press(winapi.VK["backspace"]) for _ in range(n)])
        while not lst.stopped.is_set() and not lst.paused.is_set():
            audio = record_phrase(lst.mic, lst.noise, max_wait=IDLE_S, max_len=20, silence=1.0)
            if audio is None:
                break                                   # долго молчат — диктовка закончена
            text = lst.asr.recognize(audio)
            ok, rest = find_wake(text, lst.wake, lst.is_word)
            log.info("диктовка: «%s»", text)
            if not d.feed(rest if ok else text):
                break
        self.status_cb("thinking")
        return self.phrase("Диктовка закончена.", "Рэм закончила записывать.")

    def _run(self, plan: Plan, dry_run: bool, replies: list, results: list) -> tuple[list, bool]:
        """Выполнить действия плана. → (что сделано для модели: умение, аргументы, вывод;
        нужен ли следующий шаг — умение вернуло данные)."""
        done, more = [], False
        self._failed = ""                              # что не вышло на этом шаге — модель не скажет «готово»
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
            seen = s.retell
            try:
                try:
                    out = s.handler(self.ctx, **args)
                except NotHere as e:
                    if plan.source in ("fast", "learned"):
                        raise                          # пусть решает модель — handle() переспросит её
                    out, seen = str(e), True
                    self._failed = str(e)
                # действие выбрала модель — она видит, что стало (окно после нажатия), и решает, что дальше
                if s.observe and plan.model:
                    try:
                        after = s.observe(self.ctx, **args)
                    except Exception as e:
                        after = f"окно не прочитать: {e}"
                    if after:                          # пусто — смотреть не на что, задача закончена
                        out, seen = "\n".join(x for x in (out or "", after) if x), True
                if out and not seen:
                    replies.append(out)
                results.append((name, args, "ок"))
                done.append((name, args, out or ""))
                more = more or seen
            except NotHere:
                raise
            except Cancelled:                          # «нет» на опасную кнопку внутри умения
                replies.append(self.phrase("Отменено.", "Хорошо, Рэм не будет."))
                results.append((name, args, "отменено"))
                return done, False
            except winapi.NotOnWindows:
                results.append((name, args, "только в Windows"))
            except Exception as e:
                log.exception("умение %s упало", name)
                results.append((name, args, f"ошибка: {e}"))
                if seen:                               # модель увидит ошибку и попробует иначе или объяснит
                    done.append((name, args, f"ошибка: {e}"))
                    more = True
                    self._failed = self.phrase("Не получилось: ", "Простите, у Рэм не получилось: ") + \
                        f"{s.title.lower()}."
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
