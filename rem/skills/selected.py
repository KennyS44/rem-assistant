"""Выделенный текст: прочитать, перевести, пересказать, объяснить. Только быстрым путём:
модель этого умения не видит (её подсказка не меняется), а сам текст ей отдаётся отдельно."""
from __future__ import annotations

import re

from .base import Param, skill

LANGS = ["русский", "английский", "немецкий", "французский", "испанский", "итальянский", "китайский",
         "японский", "украинский"]
READ_LIMIT = 700                              # столько символов читаем вслух, дальше — «и так далее»
ASK_LIMIT = 3000                              # столько отдаём модели (окно 4096 токенов)

PROMPTS = {
    "translate": "Переведи текст пользователя на {lang} язык. Ответь только переводом, без пояснений, "
                 "кавычек и разметки.",
    "short": "Перескажи текст пользователя по-русски в двух-трёх коротких предложениях для озвучки: "
             "только суть, без разметки и списков.",
    "explain": "Объясни по-русски простыми словами, о чём текст пользователя; если это сообщение об ошибке — "
               "что пошло не так и что сделать. Два-четыре коротких предложения для озвучки, без разметки и списков.",
}
TOKENS = {"translate": 700, "short": 160, "explain": 220}


def target_lang(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    cyr = sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in letters)
    return "английский" if letters and cyr / len(letters) >= 0.5 else "русский"


def cut(text: str, limit: int) -> str:
    """Не больше limit символов, по границе предложения."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    probe = text[:limit + 1]                  # точка ровно на границе тоже считается
    stop = max(probe.rfind(". "), probe.rfind("! "), probe.rfind("? "), head.rfind("\n"))
    return (head[:stop + 1] if stop > limit // 3 else head.rsplit(" ", 1)[0]).strip()


EXAMPLES = (
    [(p, {"action": "read"}) for p in ("прочитай это", "прочитай выделенное", "прочитай выделенный текст",
                                       "что здесь написано", "что тут написано", "прочитай вслух",
                                       "прочитай что выделено", "прочитай буфер", "что в буфере",
                                       "прочитай текст", "зачитай это")]
    + [(p, {"action": "translate"}) for p in ("переведи это", "переведи выделенное", "переведи текст",
                                              "переведи выделенный текст", "переведи что выделено",
                                              "переведи буфер", "переведи")]
    + [(f"{v} на {lang}", {"action": "translate", "lang": lang})
       for lang in LANGS for v in ("переведи", "переведи это", "переведи текст")]
    + [(p, {"action": "short"}) for p in ("сократи это", "сократи", "перескажи это", "перескажи коротко",
                                          "кратко перескажи", "о чем это", "о чем этот текст",
                                          "о чем тут написано", "перескажи выделенное", "сократи текст")]
    + [(p, {"action": "explain"}) for p in ("объясни это", "объясни", "объясни что здесь написано",
                                            "что это значит", "объясни выделенное", "объясни ошибку",
                                            "что за ошибка", "объясни текст", "что это за ошибка")]
)


@skill("selected_text", "Выделенный текст",
       "Прочитать, перевести, пересказать или объяснить выделенный текст.",
       params=[Param("action", "string", "что сделать", enum=["read", "translate", "short", "explain"]),
               Param("lang", "string", "на какой язык перевести", enum=LANGS, required=False)],
       examples=EXAMPLES, category="Окна", model=False)
def selected_text(ctx, action: str, lang: str = ""):
    text, source = ctx.selection()
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return "Ничего не выделено, и в буфере обмена пусто. Выделите текст и скажите ещё раз."
    if action == "read":
        part = cut(text, READ_LIMIT)
        return part + (" И так далее." if len(part) < len(text) else "")
    lang = lang or target_lang(text)
    answer = ctx.ask(PROMPTS[action].format(lang=lang), cut(text, ASK_LIMIT), TOKENS[action])
    if answer is None:
        return ctx.phrase("Во время игры я этого не делаю — нужна модель.",
                          "Простите, во время игры Рэм этого не делает — нужна модель.")
    answer = answer.strip()
    if not answer:
        return ctx.phrase("Не получилось — модель не ответила. Проверь, запущена ли Ollama.",
                          "Простите, модель не ответила Рэм. Проверьте, запущена ли Ollama.")
    if action == "translate":
        ctx.copy(answer)
        if lang != "русский":                 # чужой язык русским голосом не читаем — показываем
            ctx.show(answer)
            return ctx.phrase(f"Перевела на {lang} и скопировала в буфер — можно вставить.",
                              f"Рэм перевела на {lang} и скопировала в буфер — можно вставить.")
        return answer
    return answer
