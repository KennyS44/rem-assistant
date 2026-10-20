"""Модель пишет «Звук выключен», но ничего не вызывает — Рэм один раз просит вызвать функцию."""
from rem import config as cfgmod
from rem.brain import Brain
from rem.skills import active_skills

from .test_core import Steps


def test_fake_claim_is_retried_once():
    client = Steps({"actions": [], "reply": "Звук выключен."},
                   {"actions": [{"skill": "volume_mute", "args": {}}], "reply": ""})
    plan = Brain(dict(cfgmod.DEFAULTS), active_skills(cfgmod.DEFAULTS), client).plan("выключи звук")
    assert plan.actions == [("volume_mute", {})] and client.calls == 2
    assert client.seen[1][1][-1]["content"].startswith("Функция не вызвана")


def test_real_answer_is_not_retried():
    client = Steps({"actions": [], "reply": "Cat."})
    plan = Brain(dict(cfgmod.DEFAULTS), active_skills(cfgmod.DEFAULTS), client).plan("как по-английски кошка")
    assert plan.reply == "Cat." and client.calls == 1
