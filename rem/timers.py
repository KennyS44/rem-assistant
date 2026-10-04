"""Таймеры: по окончании — сигнал и голосовое сообщение."""
import threading


class Timers:
    def __init__(self, on_done):
        self.on_done = on_done              # on_done(сообщение)
        self.active: list[threading.Timer] = []
        self.lock = threading.Lock()

    def start(self, seconds: int, message: str) -> None:
        def fire():
            with self.lock:
                if t in self.active:
                    self.active.remove(t)
            self.on_done(message)

        t = threading.Timer(seconds, fire)
        t.daemon = True
        with self.lock:
            self.active.append(t)
        t.start()

    def cancel_all(self) -> int:
        with self.lock:
            n = len(self.active)
            for t in self.active:
                t.cancel()
            self.active.clear()
        return n
