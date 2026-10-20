"""Кнопки в окнах: выбор кнопки по сказанному, описание окна для модели, нажатия в несколько шагов."""
import pytest

from rem import config as cfgmod
from rem import uia
from rem.assistant import Assistant
from rem.fastpath import FastPath
from rem.skills import REGISTRY, active_skills, screen
from rem.skills.apps import AppIndex
from rem.uia import Control, best_match, dangerous, describe

from .test_core import Steps

SAVE_DIALOG = [Control("Блокнот", "текст"), Control("Сохранить изменения в файле «Безымянный»?", "текст"),
               Control("Сохранить", "кнопка"), Control("Не сохранять", "кнопка"), Control("Отмена", "кнопка")]
EN_DIALOG = [Control("Do you want to save changes?", "текст"), Control("Save", "кнопка"),
             Control("Don't Save", "кнопка"), Control("Cancel", "кнопка")]


@pytest.mark.parametrize("said,controls,want", [
    ("не сохранять", SAVE_DIALOG, "Не сохранять"),
    ("сохранить", SAVE_DIALOG, "Сохранить"),                 # точное раньше похожего «не сохранять»
    ("на кнопку отмена", SAVE_DIALOG, "Отмена"),
    ("не сохранять", EN_DIALOG, "Don't Save"),               # английская программа
    ("отмена", EN_DIALOG, "Cancel"),
    ("сохранить", [Control("Сохранить как", "меню")], "Сохранить как"),
    ("далее", [Control("Далее >", "кнопка"), Control("< Назад", "кнопка")], "Далее >"),
    ("файл", [Control("Файл", "пункт списка"), Control("Файл", "меню")], "Файл"),
])
def test_best_match(said, controls, want):
    hit = best_match(said, controls)
    assert hit is not None and hit.name == want
    if said == "файл":
        assert hit.kind == "меню"                            # меню раньше пункта списка


@pytest.mark.parametrize("said,controls", [
    ("да", [Control("Далее", "кнопка")]),                    # короткое — только точно
    ("удалить", SAVE_DIALOG),
    ("сохранить изменения в файле", SAVE_DIALOG),            # текст диалога — не кнопка
    ("", SAVE_DIALOG),
])
def test_no_match(said, controls):
    assert best_match(said, controls) is None


def test_disabled_loses_to_enabled():
    hit = best_match("ок", [Control("ОК", "кнопка", enabled=False), Control("ОК", "кнопка")])
    assert hit.enabled


def test_dangerous():
    assert dangerous("Удалить навсегда") and dangerous("Send") and dangerous("Оплатить заказ")
    assert not dangerous("Сохранить", "не сохранять") and not dangerous("Далее")
    assert dangerous("Да", texts=("Вы действительно хотите удалить этот файл?",))
    assert not dangerous("Нет", texts=("Вы действительно хотите удалить этот файл?",))
    assert not dangerous("Да", texts=("Сохранить изменения?",))


def test_describe_groups_and_fits():
    text = describe("Блокнот", "notepad.exe", SAVE_DIALOG + [Control("Файл", "меню"), Control("Файл", "меню"),
                                                             Control("Справка", "меню", enabled=False)])
    assert text.splitlines()[0] == "Окно «Блокнот» (notepad):"
    assert "Текст: «Сохранить изменения в файле «Безымянный»?»" in text      # заголовок не повторяем
    assert "кнопка: «Сохранить», «Не сохранять», «Отмена»" in text
    assert "меню: «Файл», «Справка» (неактивна)" in text
    many = describe("Браузер", "chrome.exe", [Control(f"Ссылка номер {i}", "ссылка") for i in range(200)])
    assert len(many) <= 700 and many.endswith("…")
    assert "кнопок не видно" in describe("Игра", "game.exe", [])


def test_fast_path_click():
    fp = FastPath(active_skills(cfgmod.DEFAULTS))
    (name, args), = fp.match("нажми на кнопку сохранить").actions
    assert name == "click_button" and uia.spoken_label(args["name"]) == "сохранить"
    assert fp.match("нажми не сохранять").actions == [("click_button", {"name": "не сохранять"})]
    assert fp.match("кликни далее").actions == [("click_button", {"name": "далее"})]
    assert fp.match("нажми сохранить и закрой блокнот") is None          # две просьбы — модели


def test_active_dialog_in_context(monkeypatch):
    from rem import winapi
    monkeypatch.setattr(winapi, "foreground_window", lambda: {"hwnd": 1, "pid": 1, "title": "Удаление файла", "exe": "explorer.exe"})
    monkeypatch.setattr(uia, "controls", lambda hwnds, timeout=8: [Control("Переместить в корзину?", "текст"),
                                                                   Control("Да", "кнопка"), Control("Нет", "кнопка")])
    text = screen.active_dialog(None)
    assert text.startswith("Активное окно «Удаление файла» (explorer):") and "кнопка: «Да», «Нет»" in text
    monkeypatch.setattr(uia, "controls", lambda hwnds, timeout=8: [Control(f"Ссылка {i}", "ссылка") for i in range(30)])
    assert screen.active_dialog(None) == ""                  # большое окно — только по look_at_window


class FakeScreen:
    """Блокнот: «закрыть» → диалог сохранения → «Не сохранять» → окна нет."""
    def __init__(self, monkeypatch):
        self.state, self.pressed = "dialog", []
        monkeypatch.setattr(screen, "_target", lambda ctx, window="": ({"title": "Блокнот", "exe": "notepad.exe"}, [1]))
        monkeypatch.setattr(uia, "controls", lambda hwnds, timeout=8: SAVE_DIALOG if self.state == "dialog" else [])
        monkeypatch.setattr(screen, "active_dialog", lambda ctx: "")
        monkeypatch.setattr(uia, "find_and_press", self.press)

    def press(self, hwnds, spoken, ask=None):
        c = best_match(spoken, uia.controls(hwnds))
        if c is None:
            return "missing", ""
        if ask and dangerous(c.name, spoken) and not ask(c.name):
            return "cancelled", c.name
        self.pressed.append(c.name)
        self.state = "closed"
        return "pressed", c.name


def _assistant(client, tmp_path):
    a = Assistant(dict(cfgmod.DEFAULTS), client=client, apps=AppIndex([]))
    a.journal = tmp_path / "actions.log"
    a.say = lambda t: None
    return a


def test_fast_click_does_not_ask_model(tmp_path, monkeypatch):
    fs = FakeScreen(monkeypatch)
    client = Steps({"actions": [], "reply": ""})
    _assistant(client, tmp_path).handle("нажми не сохранять")
    assert fs.pressed == ["Не сохранять"] and client.calls == 0


def test_model_sees_window_after_click_and_stops(tmp_path, monkeypatch):
    fs = FakeScreen(monkeypatch)
    client = Steps({"actions": [{"skill": "look_at_window", "args": {}}], "reply": ""},
                   {"actions": [{"skill": "click_button", "args": {"name": "Не сохранять"}}], "reply": ""},
                   {"actions": [], "reply": "Блокнот закрыт без сохранения."})
    a = _assistant(client, tmp_path)
    said = []
    a.say = said.append
    a.handle("закрой это без сохранения")
    assert fs.pressed == ["Не сохранять"] and client.calls == 3
    looked, after = client.seen[1][1][-1]["content"], client.seen[2][1][-1]["content"]
    assert "«Не сохранять»" in looked                       # модель видела кнопки
    assert after.startswith("Теперь Окно «Блокнот»") and "кнопок не видно" in after
    assert client.seen[2][1][-1]["content"] != looked and len(client.seen[2][1]) == 2   # прошлый снимок не повторяем
    assert "Уже сделано:\n- look_at_window() → Окно «Блокнот» (notepad):" in client.seen[2][0]
    assert said == ["Блокнот закрыт без сохранения."]


def test_model_retries_missing_button(tmp_path, monkeypatch):
    fs = FakeScreen(monkeypatch)
    client = Steps({"actions": [{"skill": "click_button", "args": {"name": "Закрыть без сохранения"}}], "reply": ""},
                   {"actions": [{"skill": "click_button", "args": {"name": "Не сохранять"}}], "reply": ""},
                   {"actions": [], "reply": ""})
    _assistant(client, tmp_path).handle("закрой без сохранения")
    assert "Кнопки «Закрыть без сохранения» в окне не видно." in client.seen[1][1][-1]["content"]
    assert fs.pressed == ["Не сохранять"]


def test_dangerous_button_needs_yes(tmp_path, monkeypatch):
    fs = FakeScreen(monkeypatch)
    monkeypatch.setattr(uia, "controls", lambda hwnds, timeout=8: [Control("Удалить", "кнопка")])
    client = Steps({"actions": [{"skill": "click_button", "args": {"name": "Удалить"}}], "reply": ""})
    a = _assistant(client, tmp_path)
    asked = []
    monkeypatch.setattr(a, "_confirm", lambda q: asked.append(q) or False)
    a.handle("удали это")
    assert asked == ["Нажать «Удалить»"] and fs.pressed == [] and client.calls == 1   # «нет» — дальше не идём


def test_click_skills_registered():
    assert REGISTRY["click_button"].observe and REGISTRY["look_at_window"].retell


def test_fast_click_without_button_goes_to_model(tmp_path, monkeypatch):
    """«нажми на паузу», а кнопки «паузу» в окне нет — значит, просили музыку на паузу."""
    FakeScreen(monkeypatch)
    paused = []
    monkeypatch.setattr(REGISTRY["media_play_pause"], "handler", lambda ctx: paused.append(1))
    client = Steps({"actions": [{"skill": "media_play_pause", "args": {}}], "reply": ""})
    a = _assistant(client, tmp_path)
    a.reload()
    a.handle("нажми на паузу")
    assert paused == [1] and client.calls == 1


def test_spoken_label_strips_lead_words():
    assert uia.spoken_label("по кнопке Сохранить") == "сохранить"
    assert uia.spoken_label("на вкладку вид") == "вид"
    assert uia.spoken_label("меню") == "меню"                 # одно слово не трогаем


def test_close_shows_save_dialog_to_model(tmp_path, monkeypatch):
    """«Закрой блокнот без сохранения»: блокнот спросил «Сохранить?» — модель видит диалог и жмёт."""
    fs = FakeScreen(monkeypatch)
    monkeypatch.setattr(REGISTRY["close_app"], "handler", lambda ctx, name: None)
    monkeypatch.setattr(REGISTRY["close_app"], "observe", lambda ctx, name: "Программа не закрылась, на экране "
                        + screen.look(ctx))
    client = Steps({"actions": [{"skill": "close_app", "args": {"name": "блокнот"}}], "reply": ""},
                   {"actions": [{"skill": "click_button", "args": {"name": "Не сохранять"}}], "reply": ""},
                   {"actions": [], "reply": ""})
    _assistant(client, tmp_path).handle("закрой блокнот без сохранения")
    assert "«Не сохранять»" in client.seen[1][1][-1]["content"] and fs.pressed == ["Не сохранять"]


def test_describe_shows_state():
    text = describe("Настройки", "app.exe", [Control("Автозапуск", "флажок", on=True), Control("Трей", "флажок", on=False),
                                             Control("Принимаю", "переключатель", on=True),
                                             Control("Не принимаю", "переключатель", on=False)])
    assert "флажок: «Автозапуск» (отмечен), «Трей» (не отмечен)" in text
    assert "переключатель: «Принимаю» (выбрано), «Не принимаю»" in text


def test_same_click_twice_stops(tmp_path, monkeypatch):
    """Окно не меняется, а модель жмёт одно и то же — останавливаемся, а не жмём 8 раз."""
    fs = FakeScreen(monkeypatch)
    monkeypatch.setattr(uia, "controls", lambda hwnds, timeout=8: [Control("Принимаю", "переключатель", on=True)])
    fs.press = lambda hwnds, spoken, ask=None: (fs.pressed.append(spoken), ("pressed", spoken))[1]
    monkeypatch.setattr(uia, "find_and_press", fs.press)
    client = Steps({"actions": [{"skill": "click_button", "args": {"name": "Принимаю"}}], "reply": ""})
    a = _assistant(client, tmp_path)
    said = []
    a.say = said.append
    a.handle("согласись")
    assert len(fs.pressed) == 2 and said == ["Не получилось."]
