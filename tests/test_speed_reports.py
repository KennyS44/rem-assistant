"""Быстрее и надёжнее: больше команд без модели, ослышки в названиях, отчёт разработчику."""
import json
from urllib.parse import unquote

import pytest

from rem import config as cfgmod
from rem import report
from rem.fastpath import FastPath, base_forms, find_site
from rem.skills import active_skills
from rem.skills.apps import AppIndex
from rem.skills.system import parse_duration


@pytest.fixture(scope="module")
def fast():
    return FastPath(active_skills(dict(cfgmod.DEFAULTS)))


@pytest.mark.parametrize("said, skill, args", [
    ("сколько время", "tell_time", {}),
    ("который сейчас час", "tell_time", {}),
    ("какой сегодня день недели", "tell_date", {}),
    ("поставь таймер на полчаса", "set_timer", {"minutes": 30}),
    ("таймер на час", "set_timer", {"minutes": 60}),
    ("таймер на 30 секунд", "set_timer", {"seconds": 30}),
    ("засеки полторы минуты", "set_timer", {"minutes": 1, "seconds": 30}),
    ("поставь таймер на две минуты", "set_timer", {"minutes": 2}),
    ("включи ютуб", "open_site", {"url": "youtube.com"}),
    ("открой ютюб", "open_site", {"url": "youtube.com"}),
    ("зайди в вк", "open_site", {"url": "vk.com"}),
    ("громкость на максимум", "volume_set", {"level": 100}),
    ("сделай звук громче", "volume_up", {}),
    ("сделай звук тише", "volume_down", {}),
    ("верни звук", "volume_mute", {}),
    ("следующая", "media_next", {}),
    ("переключи песню", "media_next", {}),
    ("поставь паузу", "media_play_pause", {}),
    ("запусти телегу", "open_app", {"name": "телегу"}),
    ("сделай скриншот экрана", "screenshot", {}),
    ("заблокируй экран", "lock_pc", {}),
    ("Спасибо!", "small_talk", {"kind": "thanks"}),
    ("привет", "small_talk", {"kind": "hello"}),
    ("сообщи об ошибке", "report_problem", {"note": ""}),
    ("сообщи об ошибке не включилась музыка", "report_problem", {"note": "не включилась музыка"}),
])
def test_without_model(fast, said, skill, args):
    plan = fast.match(said)
    assert plan is not None and plan.actions == [(skill, args)], plan


@pytest.mark.parametrize("said", ["таймер на завтра", "включи звук в браузере", "открой стимул",
                                  "спасибо, а теперь открой ютуб", "напиши ошибку в поле"])
def test_doubtful_still_goes_elsewhere(fast, said):
    plan = fast.match(said)
    assert plan is None or plan.actions[0][0] not in ("set_timer", "small_talk", "report_problem", "open_site"), plan


@pytest.mark.parametrize("text, seconds", [("полчаса", 1800), ("1 час 20 минут", 4800), ("2 минуты 30 секунд", 150),
                                           ("минуту", 60), ("5", 300), ("полтора часа", 5400), ("завтра", None),
                                           ("5 минут 3", None)])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


def test_sites_and_apps_in_other_case():
    assert find_site("почту") == "mail.google.com" and find_site("ютюб") == "youtube.com"
    assert find_site("стим") is None and find_site("телега") is None
    assert "телега" in base_forms("телегу")
    assert AppIndex([]).resolve("калькулятором")[0] == "калькулятор"


def _journal(tmp_path, recs):
    p = tmp_path / "actions.log"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs), encoding="utf-8")
    return p


def test_report_hides_messages_and_marks_wrong(tmp_path):
    j = _journal(tmp_path, [
        {"time": "2026-11-14T10:00:00", "text": "открой ютуб", "source": "fast",
         "actions": [["open_site", {"url": "youtube.com"}, "ок"]], "reply": "", "total_s": 0.3},
        {"time": "2026-11-14T10:01:00", "text": "напиши маме что я опоздаю", "source": "fast",
         "actions": [["send_message", {"said": "напиши маме что я опоздаю"}, "ок"]], "reply": "Отправлено."},
        {"time": "2026-11-14T10:02:00", "text": "включи предыдущую вкладку", "source": "llm",
         "actions": [["switch_tab", {"name": "1"}, "ок"]], "reply": "", "total_s": 4.1},
        {"time": "2026-11-14T10:02:10", "text": "включи предыдущую вкладку", "wrong": True},
    ])
    (tmp_path / "rem.log").write_text("ок\nERROR умение music упало: нет окна\n", encoding="utf-8")
    url = report.issue_url("0.6.3", j, tmp_path / "rem.log", "вкладки")
    text = unquote(url)
    assert url.startswith(report.ISSUES) and "Отчёт Рэм 0.6.3: вкладки" in text
    assert "опоздаю" not in text and "(сообщение скрыто)" in text
    assert "❌ включи предыдущую вкладку | модель" in text and text.count("включи предыдущую вкладку") == 1
    assert "ERROR умение music упало" in text


def test_report_fits_in_url(tmp_path):
    recs = [{"time": "2026-11-14T10:00:00", "text": "очень длинная команда " * 5, "source": "llm",
             "actions": [["open_app", {"name": "x" * 40}, "ок"]], "reply": "ответ " * 20} for _ in range(200)]
    url = report.issue_url("0.6.3", _journal(tmp_path, recs), tmp_path / "нет.log")
    assert len(url) <= report.MAX_URL + 300
    page = report.open_report("0.6.3", tmp_path)
    assert page.exists() and "location.replace" in page.read_text(encoding="utf-8")


def test_prewarm_once_a_minute(monkeypatch):
    from rem.assistant import Assistant

    class Client:
        n = 0

        def warm(self, *a, **k):
            Client.n += 1

    a = Assistant.__new__(Assistant)
    a.config, a.game_mode, a.client, a._warmed_at = {"model": "m"}, False, Client(), -1e9
    monkeypatch.setattr("threading.Thread", lambda target, daemon: type("T", (), {"start": lambda s: target()})())
    a.prewarm()
    a.prewarm()
    assert Client.n == 1
