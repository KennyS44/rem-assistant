"""Отчёт разработчику: последние команды из журнала и ошибки из rem.log — готовой заявкой на GitHub.

Заявка открывается в браузере уже заполненной; человек видит текст и сам нажимает «Submit».
Тексты сообщений и диктовки в отчёт не попадают — только то, какие это были команды."""
from __future__ import annotations

import json
import platform
import re
from pathlib import Path
from urllib.parse import quote

ISSUES = "https://github.com/KennyS44/rem-assistant/issues/new"
MAX_URL = 7500                      # длиннее браузеры и GitHub обрезают
PRIVATE = {"send_message", "type_text", "dictation", "run_command"}     # их тексты — личное
SOURCE = {"fast": "сама", "learned": "выучено", "llm": "модель", "cloud": "облако"}


def _cell(text: str, limit: int = 60) -> str:
    text = " ".join(str(text).split()).replace("|", "/")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _actions(rec: dict) -> str:
    out = []
    for a in rec.get("actions", []):
        if isinstance(a, (list, tuple)) and len(a) >= 3:
            name, args, status = a[0], a[1], a[2]
            if name in PRIVATE:
                args = {"…": "скрыто"}
            short = ", ".join(f"{v}" for v in (args or {}).values())[:30]
            out.append(f"{name}({short}) {'' if status == 'ок' else '— ' + str(status)}".strip())
    return "; ".join(out) or "—"


def _private(rec: dict) -> bool:
    return any(isinstance(a, (list, tuple)) and a and a[0] in PRIVATE for a in rec.get("actions", []))


def read_journal(path: Path, n: int = 40) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-n * 2:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    marked = []                                     # «не то» — отметить саму команду, а не отдельной строкой
    for r in out:
        if r.get("wrong") and not r.get("actions"):
            prev = next((p for p in reversed(marked) if p.get("text") == r.get("text")), None)
            if prev is not None:
                prev["wrong"] = True
                continue
        marked.append(r)
    return marked[-n:]


def log_errors(path: Path, n: int = 8) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    bad = [ln for ln in lines if re.search(r"ERROR|упало|Traceback|Exception", ln)]
    return [_cell(ln, 160) for ln in bad[-n:]]


def body(version: str, records: list[dict], errors: list[str], note: str = "") -> str:
    head = [f"**Версия Рэм:** {version}, Windows {platform.release()} ({platform.version()})", ""]
    if note:
        head += [f"**Что случилось:** {note}", ""]
    head += ["_Заявка публичная — проверьте текст перед отправкой. Тексты сообщений и диктовки скрыты._", "",
             "| Когда | Сказано | Кто понял | Что сделано | Ответ | с |", "|---|---|---|---|---|---|"]
    rows = []
    for r in records:
        mark = "❌ " if r.get("wrong") else ""
        if r.get("wrong") and not r.get("actions"):
            rows.append(f"| {r.get('time', '')[5:16]} | ❌ «{_cell(r.get('text', ''))}» отмечено «не то» | | | | |")
            continue
        said = "(сообщение скрыто)" if _private(r) else _cell(r.get("text", ""))
        reply = "" if _private(r) else _cell(r.get("reply", ""), 50)
        rows.append(f"| {r.get('time', '')[5:16]} | {mark}{said} | {SOURCE.get(r.get('source'), r.get('source', ''))}"
                    f"{' (ошибка модели)' if r.get('error') else ''} | {_cell(_actions(r), 70)} | {reply} | "
                    f"{r.get('total_s', '')} |")
    tail = ["", "**Ошибки из rem.log:**", "```", *errors, "```"] if errors else []
    while rows:                                     # не длиннее, чем пропустит адрес
        text = "\n".join(head + rows + tail)
        if len(quote(text)) <= MAX_URL - 200:
            return text
        rows.pop(0)
    return "\n".join(head + tail)[:2000]


def issue_url(version: str, journal: Path, log: Path, note: str = "") -> str:
    title = f"Отчёт Рэм {version}" + (f": {_cell(note, 50)}" if note else "")
    text = body(version, read_journal(journal), log_errors(log), note)
    return f"{ISSUES}?title={quote(title)}&body={quote(text)}"


def open_report(version: str, folder: Path, note: str = "") -> Path:
    """Страница-переходник с заполненной заявкой: длинный адрес через «Пуск» обрезался бы.
    Возвращает путь к ней — открыть программой по умолчанию."""
    import html
    url = issue_url(version, folder / "actions.log", folder / "rem.log", note)
    page = folder / "report.html"
    page.write_text(
        '<!doctype html><meta charset="utf-8"><title>Отчёт Рэм</title>'
        f'<script>location.replace({json.dumps(url)})</script>'
        f'<p style="font:16px sans-serif">Открываю заявку на GitHub… '
        f'<a href="{html.escape(url)}">Если не открылась — нажмите сюда</a>.</p>', encoding="utf-8")
    return page
