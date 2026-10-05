"""Оценка «мозга» на наборе русских команд. Требует запущенную Ollama.

    python -m tests.eval_brain --model qwen3:4b-instruct-2507-q4_K_M [--url http://127.0.0.1:11434]
"""
import argparse
import json
import statistics
from pathlib import Path

from rem.brain import Brain, Ollama
from rem.config import DEFAULTS
from rem.skills import active_skills

CASES = json.loads((Path(__file__).parent / "commands_ru.json").read_text(encoding="utf-8"))


def _arg_ok(want, got) -> bool:
    if isinstance(want, int):
        return got == want
    alts = [a.strip().lower() for a in str(want).split("|")]
    return any(a in str(got).lower() for a in alts)


def matches(expect: list, actions: list) -> bool:
    if len(expect) != len(actions):
        return False
    for (skill, args), (got_skill, got_args) in zip(expect, actions):
        if skill != got_skill or not all(_arg_ok(v, got_args.get(k)) for k, v in args.items()):
            return False
    return True


def run(model: str, url: str, mode: str = "tools", verbose: bool = True,
        rem_style: bool = False) -> tuple[int, int, list[float]]:
    cfg = {**DEFAULTS, "model": model, "brain_mode": mode, "rem_style": rem_style}
    brain = Brain(cfg, active_skills(cfg), Ollama(url, timeout=300))
    brain.plan("громче")                       # прогрев: загрузка модели и кэш промпта
    ok, times = 0, []
    for case in CASES:
        plan = brain.plan(case["text"])
        times.append(plan.seconds)
        good = matches(case["expect"], plan.actions) or any(
            matches(alt, plan.actions) for alt in case.get("also", []))
        ok += good
        if verbose and not good:
            print(f"  ✗ «{case['text']}» → {plan.actions} {plan.reply!r} {plan.error}", flush=True)
    return ok, len(CASES), times


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--mode", action="append", choices=["tools", "schema"])
    ap.add_argument("--rem-style", action="store_true", help="промпт «в стиле Рем»")
    a = ap.parse_args()
    for m, mode in [(m, mode) for m in a.model for mode in a.mode or ["tools"]]:
        print(f"== {m} [{mode}]", flush=True)
        ok, n, t = run(m, a.url, mode, rem_style=a.rem_style)
        print(f"   точность {ok}/{n} ({ok / n * 100:.0f}%) | медиана {statistics.median(t):.2f} с, "
              f"макс {max(t):.2f} с (на этом процессоре)", flush=True)
