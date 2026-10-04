"""Запуск Рэм.

    Rem.exe                      — обычный режим: значок в трее, слушает микрофон
    Rem.exe --text "громче"      — выполнить одну команду текстом (без микрофона)
    Rem.exe --text "..." --dry   — только показать, как понята команда
    Rem.exe --selftest           — самопроверка для автосборки (без микрофона и сети)
    Rem.exe --voice-test         — проверка нейроголоса (скачивает его)
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import threading

from . import __version__, config as cfgmod, winapi


def setup_logging(console: bool) -> logging.Logger:
    log = logging.getLogger("rem")
    log.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(cfgmod.app_dir() / "rem.log", maxBytes=1_000_000,
                                              backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        log.addHandler(sh)
    return log


def single_instance() -> bool:
    """Вторая копия Рэм слушала бы тот же микрофон — не даём запустить."""
    if not winapi.IS_WINDOWS:
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\RemAssistantMutex")
    return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS


class App:
    def __init__(self, log: logging.Logger):
        self.log = log
        self.config = cfgmod.load()
        self.state = "idle"
        self.test_mode = False
        self.stop = threading.Event()
        self.listener = None
        self.speakers = None
        self.assistant = None
        self.ui = None
        self.pending_update = None

    # ——— запуск ———

    def run(self) -> int:
        from .assistant import Assistant
        from .speech import Sounds, Voice
        from .ui import UI

        winapi.set_low_priority()
        self.ui = UI(self)
        self.voice = Voice(config=self.config)
        self.voice.on_problem = lambda text: self.ui.call(self.ui.notify, text)
        self.sounds = Sounds(cfgmod.app_dir() / "sounds")
        self.assistant = Assistant(self.config, self.voice, self.sounds)
        self.assistant.status_cb = lambda s: self.ui.call(self._set_state, s)
        self.assistant.notify_cb = lambda text: self.ui.call(self.ui.notify, text)
        threading.Thread(target=self._boot, name="boot", daemon=True).start()
        self.ui.root.mainloop()
        return 0

    def _boot(self) -> None:
        """Подготовка в фоне: модели речи, Ollama, микрофон — и в работу."""
        from . import models
        from .echo import SpeakerTap
        from .listen import ASR, Listener, Microphone, pick_input

        try:
            if not models.speech_models_ready():
                win, update, close = self._call_sync(self.ui.progress_window,
                                                     "Скачиваю модели речи (один раз)")
                try:
                    models.ensure_speech_models(update)
                finally:
                    close()
            self._ensure_ollama()

            import vosk
            vosk.SetLogLevel(-1)
            vmodel = vosk.Model(str(models.vosk_dir()))
            asr = ASR(models.gigaam_dir(), int(self.config.get("asr_threads", 3)))
            mic = Microphone(pick_input(self.config.get("mic_device")))
            mic.start()
        except Exception as e:
            self.log.exception("запуск не удался")
            msg = f"Рэм не смог запуститься:\n{e}"     # e исчезнет после except, а окно — позже
            self.ui.call(lambda: self._fatal(msg))
            return

        self.speakers = SpeakerTap()
        if self.config.get("ignore_speakers", True):
            self.speakers.start()
        self.listener = Listener(vmodel, asr, self.config["wake_word"], mic,
                                 speakers=self.speakers if self.speakers.available else None,
                                 mute=self.voice.speaking)
        self.assistant.listener = self.listener
        self.ui.call(self.ui.start_tray)
        threading.Thread(target=self.assistant.watch_game_mode, args=(self.stop,), daemon=True).start()
        threading.Thread(target=self.listener.listen, name="listen", daemon=True,
                         args=(self._on_command, self._on_wake_only, None)).start()
        self.log.info("Рэм %s слушает, слово активации «%s»", __version__, self.config["wake_word"])
        self.ui.call(self.ui.notify, f"Рэм запущен. Скажи «{self.config['wake_word'].capitalize()}» и команду.")
        threading.Thread(target=self._update_loop, name="update", daemon=True).start()

    # ——— обновления ———

    def _update_loop(self) -> None:
        from . import update
        if not update.can_update():
            return
        update.cleanup()
        delay = 60                                  # не мешаем запуску
        while not self.stop.wait(delay):
            delay = 6 * 3600
            if self.config.get("auto_update", True):
                self.check_update(quiet=True)

    def check_update(self, quiet: bool = False) -> None:
        """Найти, скачать и предложить новую версию. quiet — молчать, если обновлений нет."""
        from . import update
        try:
            rel = update.latest()
            if not rel or not update.newer(rel.version):
                if not quiet:
                    self.ui.call(self.ui.notify, f"Установлена последняя версия Рэм ({__version__}).")
                return
            path = update.download(rel)
        except Exception as e:
            self.log.warning("обновление: %s", e)
            if not quiet:
                self.ui.call(self.ui.notify, "Не удалось проверить обновления — нет связи с GitHub.")
            return
        self.log.info("скачано обновление %s", rel.version)
        self.pending_update = (rel.version, path)
        self.ui.call(self.ui.offer_update, rel.version, path)

    def install_update(self, path) -> None:
        from . import update
        update.install(path)
        self.quit()

    def _ensure_ollama(self) -> None:
        cl = self.assistant.client
        if not cl.available() and cl.start_local():
            self.log.info("Ollama была не запущена — запустил сам")
        while not cl.available():
            if not self._call_sync(self.ui.ask_ollama):
                self.log.warning("Ollama не запущена — работаю только с простыми командами")
                return
        model = self.config["model"]
        if model not in cl.models():
            win, update, close = self._call_sync(self.ui.progress_window,
                                                 f"Скачиваю модель {model} (один раз, ~2,5 ГБ)")
            try:
                cl.pull(model, lambda f, s: update(f, "Языковая модель"))
            finally:
                close()

    def _call_sync(self, fn, *args):
        """Выполнить fn в потоке интерфейса и дождаться результата."""
        box, done = {}, threading.Event()

        def go():
            try:
                box["r"] = fn(*args)
            finally:
                done.set()
        self.ui.call(go)
        done.wait()
        return box.get("r")

    def _fatal(self, text: str) -> None:
        from tkinter import messagebox
        messagebox.showerror("Рэм", text)
        self.quit()

    # ——— события слуха ———

    def _on_command(self, text: str) -> None:
        if self.test_mode:
            return
        self.assistant.handle(text)

    def _on_wake_only(self) -> None:
        if self.test_mode:
            return
        self._set_state("listening")
        if self.config.get("rem_style"):
            self.voice.say("Рэм слушает.")
            self.voice.wait()
            self.listener.mic.flush()
        else:
            self.sounds.play("wake")
        self.assistant.prewarm()                # пока человек говорит — грузим модель
        command = self.listener.hear_command()
        if command:
            self.assistant.handle(command)
        else:
            self._set_state("game" if self.assistant.game_mode else "idle")

    # ——— управление ———

    def _set_state(self, s: str) -> None:
        if self.state == "paused" and s in ("idle", "game"):
            return
        self.state = s
        if self.ui:
            self.ui.set_state(s)

    def toggle_pause(self) -> None:
        if not self.listener:
            return
        if self.listener.paused.is_set():
            self.listener.paused.clear()
            self.listener.mic.start()
            self.state = "idle"
        else:
            self.listener.paused.set()
            self.listener.mic.stop()            # микрофон по-настоящему выключен
            self.state = "paused"
        self.ui.call(self.ui.set_state, self.state)

    def apply_config(self, old: dict | None = None) -> None:
        """После сохранения настроек. old — настройки до изменения."""
        old = old or {}
        self.assistant.config = self.config
        self.assistant.reload()
        self.voice.configure(self.config)
        if self.listener:
            self.listener.set_wake(self.config["wake_word"])
            if old.get("mic_device") != self.config.get("mic_device"):
                self._switch_mic()
            if old.get("ignore_speakers", True) != self.config.get("ignore_speakers", True):
                if self.config.get("ignore_speakers", True):
                    self.speakers.start()
                else:
                    self.speakers.stop()
                self.listener.speakers = self.speakers if self.speakers.available else None
        if self.config["model"] not in (self.assistant.client.models() if self.assistant.client.available() else []):
            threading.Thread(target=self._ensure_ollama, daemon=True).start()

    def _switch_mic(self) -> None:
        from .listen import Microphone, pick_input
        old = self.listener.mic
        old.stop()
        mic = Microphone(pick_input(self.config.get("mic_device")))
        try:
            mic.start()
        except Exception as e:
            self.log.warning("микрофон не открылся: %s — возвращаю прежний", e)
            self.ui.call(self.ui.notify, "Этот микрофон не открылся — оставил прежний.")
            mic = Microphone(old.device)
            mic.start()
        self.listener.mic = mic

    def quit(self) -> None:
        self.stop.set()
        if self.listener:
            self.listener.stopped.set()
            self.listener.mic.stop()
        if self.speakers:
            self.speakers.stop()
        if self.ui and self.ui.icon:
            self.ui.icon.stop()
        self.ui.root.after(100, self.ui.root.destroy)


# ——— режимы без интерфейса ———

def run_text(text: str, dry: bool) -> int:
    from .assistant import Assistant
    cfg = cfgmod.load()
    a = Assistant(cfg)
    plan = a.handle(text, dry_run=dry)
    print(f"источник: {plan.source}  ({plan.seconds:.2f} с)" if plan.source == "llm" else f"источник: {plan.source}")
    for name, args in plan.actions:
        print(f"  → {name} {args}")
    if plan.reply:
        print(f"  ответ: {plan.reply}")
    if plan.error:
        print(f"  ошибка: {plan.error}")
    return 0 if not plan.error else 1


def selftest(report: str | None = None) -> int:
    """Проверки, которые можно выполнить на сервере сборки: без микрофона, звука и сети.
    report — файл для отчёта (у собранного Rem.exe нет консоли)."""
    import json
    import tempfile
    from pathlib import Path

    from .brain import build_schema, build_system_prompt, validate
    from .fastpath import FastPath
    from .listen import find_wake
    from .skills import active_skills
    from .speech import _tone_wav

    ok = True
    lines: list[str] = []

    if report:
        Path(report).write_text("", encoding="utf-8")

    def emit(line):
        lines.append(line)
        print(line)
        if report:                       # построчно: при зависании видно, где застряли
            with open(report, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def check(name, cond):
        nonlocal ok
        emit(("OK   " if cond else "FAIL ") + name)
        ok = ok and bool(cond)

    cfg = dict(cfgmod.DEFAULTS)
    skills = active_skills(cfg)
    check("умения загружены", len(skills) >= 20)
    check("схема ответа строится", len(json.dumps(build_schema(skills))) > 1000)
    check("системный промпт", "open_app" in build_system_prompt(skills))
    fp = FastPath(skills)
    check("быстрый путь: громкость 30", fp.match("громкость 30").actions == [("volume_set", {"level": 30})])
    check("быстрый путь: открой ≠ закрой", fp.match("закрой загрузки") is None)
    check("проверка ответа модели", validate({"actions": [{"skill": "volume_set", "args": {"level": 140}}],
                                              "reply": ""}, skills).actions == [("volume_set", {"level": 100})])
    check("слово активации", find_wake("Рэм, открой браузер.", "рэм") == (True, "открой браузер"))
    check("ложное слово отсеяно", find_wake("Тремя друзьями", "рэм")[0] is False)
    check("звуки генерируются", len(_tone_wav([(440, 0.1)])) > 1000)
    import numpy as np
    from .echo import to_mono_16k
    from .speech import sapi_params
    from .update import from_api, newer
    stereo48 = np.zeros(4800 * 2, dtype=np.int16).tobytes()
    check("звук колонок: 48 кГц стерео → 16 кГц моно", len(to_mono_16k(stereo48, 2, 48000)) == 1600)
    check("высота и темп голоса Windows", sapi_params(0, 100) == (0, 1) and sapi_params(30, 140) == (10, 10))
    check("сравнение версий", newer("0.10.0", "0.9.9") and not newer("v0.1.0", "0.1.0"))
    check("релиз с установщиком", from_api({"tag_name": "v9.0.0", "assets": [
        {"name": "RemSetup.exe", "browser_download_url": "u", "size": 1}]}).version == "9.0.0")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.json"
        cfgmod.save({**cfg, "wake_word": "пятница"}, p)
        check("настройки сохраняются", cfgmod.load(p)["wake_word"] == "пятница")
    if winapi.IS_WINDOWS:
        check("папка «Загрузки» находится", bool(winapi.known_folder("downloads")))
        check("проверка полноэкранного окна", winapi.foreground_is_fullscreen() in (True, False))
        import importlib
        for mod in ("vosk", "onnx_asr", "onnxruntime", "sounddevice", "pystray", "pycaw.pycaw",
                    "comtypes.client", "psutil", "PIL.ImageGrab", "pyaudiowpatch"):
            try:
                importlib.import_module(mod)
                check(f"модуль {mod}", True)
            except Exception as e:
                check(f"модуль {mod}: {e}", False)
        emit("…проверяю голос Windows")
        try:
            from .speech import Voice
            v = Voice()
            v.say("проверка")      # на сервере без звука — без ошибок, просто тишина
            v.wait()
            check(f"голос Windows (голосов в системе: {len(v.voices)})", True)
        except Exception as e:
            check(f"голос Windows: {e}", False)
        try:
            from . import models
            check("модели: путь к папке", models.models_dir().exists())
        except Exception as e:
            check(f"модели: {e}", False)
        import onnx_asr
        data = Path(onnx_asr.__file__).parent / "preprocessors" / "data" / "gigaam_v3_conv.onnx"
        check("данные onnx_asr внутри сборки", data.exists())
    emit("ИТОГ: " + ("всё в порядке" if ok else "есть ошибки"))
    return 0 if ok else 1


def _utf8_console() -> None:
    """Консоль Windows по умолчанию в cp1252/cp866 — кириллица в ней падает с ошибкой."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def speech_test(wavs: list[str], report: str | None = None) -> int:
    """Настоящее распознавание на записанных фразах: модели, Vosk, GigaAM, проверка слова.
    Для автосборки — проверяет, что библиотеки речи работают внутри собранного Rem.exe.
    Ожидание задаётся именем файла: wake_*.wav — должен услышать, остальные — отсеять."""
    import json
    import wave
    from pathlib import Path

    import numpy as np
    import vosk

    from . import models
    from .listen import ASR, CHUNK, WakeGuard, find_wake, wake_from_speakers

    out: list[str] = []

    def emit(line):
        out.append(line)
        print(line)
        if report:
            with open(report, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    if report:
        Path(report).write_text("", encoding="utf-8")
    emit("…скачиваю модели речи (если их нет)")
    models.ensure_speech_models()
    vosk.SetLogLevel(-1)
    vm = vosk.Model(str(models.vosk_dir()))
    asr = ASR(models.gigaam_dir(), 3)
    is_word = lambda w: vm.vosk_model_find_word(w) >= 0
    ok = True
    for path in wavs:
        with wave.open(path) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        guard = WakeGuard(vm, "рэм")
        heard = None
        for i in range(0, len(audio) - CHUNK + 1, CHUNK):
            heard = heard if heard is not None else guard.feed(audio[i:i + CHUNK])
        tail = json.loads(guard.rec.FinalResult()).get("text", "").split()
        if heard is None and "рэм" in tail:
            heard = audio
        text = asr.recognize(audio)
        found, command = find_wake(text, "рэм", is_word)
        want = Path(path).name.startswith("wake")
        good = found == want and (not want or bool(command))
        ok = ok and good
        emit(f"{'OK  ' if good else 'FAIL'} {Path(path).name}: сторож={'да' if heard is not None else 'нет'}, "
             f"распознано «{text}» → {'команда «' + command + '»' if found else 'отсеяно'}")
        # та же запись «из колонок»: слово в фильме — Рэм не просыпается; тишина — не мешает
        echo = wake_from_speakers(asr, audio, "рэм", is_word)
        quiet = wake_from_speakers(asr, np.zeros_like(audio), "рэм", is_word)
        good = echo == want and not quiet
        ok = ok and good
        emit(f"{'OK  ' if good else 'FAIL'} {Path(path).name} из колонок: "
             f"{'слово найдено — не реагирую' if echo else 'слова нет'}; тихие колонки не мешают: {not quiet}")
    emit("ИТОГ: " + ("речь работает" if ok else "есть ошибки"))
    return 0 if ok else 1


def voice_test(report: str | None = None) -> int:
    """Нейроголос целиком: скачать (как по кнопке в настройках), запустить RemVoice,
    озвучить фразу каждым голосом. Для автосборки — без проигрывания звука."""
    import wave
    from pathlib import Path

    from . import voicepack
    from .speech import PREVIEW, SILERO_VOICES

    if report:
        Path(report).write_text("", encoding="utf-8")

    def emit(line):
        print(line)
        if report:
            with open(report, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    emit("…скачиваю нейроголос (если его нет)")
    voicepack.ensure()
    v = voicepack.NeuralVoice()
    ok = True
    try:
        for key, title in SILERO_VOICES.items():
            wav = v.synth(PREVIEW, key, 15, 90)
            with wave.open(str(wav)) as w:
                secs = w.getnframes() / w.getframerate()
            good = secs > 1.5
            ok = ok and good
            emit(f"{'OK  ' if good else 'FAIL'} {title}: {secs:.1f} с")
        try:
            v.synth("проверка", "nobody")
            ok = False
            emit("FAIL неизвестный голос не вызвал ошибку")
        except RuntimeError:
            emit("OK   неизвестный голос — понятная ошибка")
    finally:
        v.close()
    emit("ИТОГ: " + ("голос работает" if ok else "есть ошибки"))
    return 0 if ok else 1


def main() -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(prog="rem", description="Рэм — локальный голосовой помощник")
    ap.add_argument("--text", help="выполнить команду текстом")
    ap.add_argument("--dry", action="store_true", help="только показать план")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--report", help="файл для отчёта самопроверки")
    ap.add_argument("--speech-test", nargs="+", metavar="WAV", help="проверка распознавания на записях")
    ap.add_argument("--check-ollama", action="store_true", help="запустить Ollama, если не запущена")
    ap.add_argument("--voice-test", action="store_true", help="проверка нейроголоса (скачает его)")
    ap.add_argument("--console", action="store_true", help="писать журнал в консоль")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.report)
    if a.speech_test:
        return speech_test(a.speech_test, a.report)
    if a.voice_test:
        return voice_test(a.report)
    if a.check_ollama:
        from .brain import Ollama
        ok = Ollama(cfgmod.load().get("ollama_url", "http://127.0.0.1:11434")).start_local()
        if a.report:
            from pathlib import Path
            Path(a.report).write_text("Ollama отвечает" if ok else "Ollama не запустилась", encoding="utf-8")
        return 0 if ok else 1
    log = setup_logging(a.console or bool(a.text))
    if a.text:
        return run_text(a.text, a.dry)
    if not single_instance():
        return 0
    return App(log).run()


if __name__ == "__main__":
    sys.exit(main())
