"""RemVoice — нейросетевой голос Silero для Рэм, отдельной программой.

Silero работает на torch, а это сотни мегабайт. Поэтому голос — отдельная загрузка
(%APPDATA%\\Rem\\voice), а Rem.exe и его обновления остаются маленькими.

Протокол: по строке JSON на stdin, по строке JSON на stdout.
    → {"text": "...", "speaker": "xenia", "pitch": 10, "rate": 100, "out": "C:\\...\\say.wav"}
    ← {"ok": true, "seconds": 2.4}  или  {"ok": false, "error": "..."}
Первая строка после запуска: {"ready": true, "speakers": [...]} или {"ready": false, "error": "..."}.

    RemVoice.exe --model v5_ru.pt              — сервер
    RemVoice.exe --model v5_ru.pt --say "текст" --out a.wav   — одна фраза (для проверки)
"""
import argparse
import json
import sys
import wave
from xml.sax.saxutils import escape

RATE = 24000


def load(model_path):
    import torch
    torch.set_num_threads(3)
    model = torch.package.PackageImporter(model_path).load_pickle("tts_models", "model")
    return model


def synth(model, text, speaker="xenia", pitch=0, rate=100, out="say.wav"):
    """pitch — сдвиг высоты в процентах (-30…+30), rate — темп в процентах (60…140)."""
    pitch = max(-30, min(30, int(pitch)))
    rate = max(60, min(140, int(rate)))
    ssml = (f'<speak><prosody pitch="{pitch:+d}%" rate="{rate}%">{escape(text)}</prosody></speak>')
    audio = model.apply_tts(ssml_text=ssml, speaker=speaker, sample_rate=RATE).numpy()
    pcm = (audio.clip(-1, 1) * 32767).astype("<i2")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return len(pcm) / RATE


def main():
    for s in (sys.stdin, sys.stdout):
        s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--say")
    ap.add_argument("--out", default="say.wav")
    ap.add_argument("--speaker", default="xenia")
    a = ap.parse_args()

    def send(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    try:
        model = load(a.model)
    except Exception as e:
        send({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 1
    if a.say:
        send({"ok": True, "seconds": synth(model, a.say, a.speaker, out=a.out)})
        return 0
    send({"ready": True, "speakers": list(model.speakers)})
    for line in sys.stdin:
        try:
            req = json.loads(line)
            secs = synth(model, req["text"], req.get("speaker", "xenia"), req.get("pitch", 0),
                         req.get("rate", 100), req["out"])
            send({"ok": True, "seconds": secs})
        except Exception as e:
            send({"ok": False, "error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
