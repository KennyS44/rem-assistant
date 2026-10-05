"""Голос Рем по текстовому описанию (Qwen3-TTS, Apache 2.0) — для разработчика, не входит в Рэм.

Ничьи записи не используются: голос создаётся только по описанию VARIANTS.
Модели на 1,7 млрд параметров нужно ~10 ГБ памяти, поэтому запускается в GitHub Actions
(.github/workflows/voice-design.yml), результат — архив с WAV.

    python voice/design.py samples out/            — по 2 образца каждого варианта описания
    python voice/design.py samples out/ --engine vox   — то же моделью VoxCPM2 (Apache 2.0)
    python voice/design.py pack out/ --variant b --seed 2
        — образец выбранного голоса → клон этого образца (модель Base) → все фразы
          из rem.voiceclips.phrases() в out/clips/<ключ>.wav
"""
import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rem import voiceclips  # noqa: E402

REF_TEXT = "Рэм слушает. Скажите, что нужно сделать, и Рэм всё сделает. Хорошо, таймер на пять минут поставлен."
BASE = ("Young woman around eighteen, high clear voice, soft and gentle, calm and polite like a devoted maid, "
        "warm and tender, speaks a little slowly and carefully, light breathiness, Japanese anime heroine style.")
VARIANTS = {
    "a": BASE,
    "b": BASE.replace("high clear voice", "fairly high but quiet voice")
         + " Serene, reserved, almost whispering at phrase ends.",
    "c": BASE + " Slightly brighter and sweeter, with a faint smile in the voice.",
    "d": BASE.replace("high clear voice", "very high, light, youthful voice") + " Cute and earnest.",
    "e": "Teenage girl with a soft, mid-high, slightly husky voice, quiet and composed, devoted and "
         "affectionate, polite formal speech, Japanese anime voice acting, gentle smile, calm pace.",
    "f": "Gentle young maid, sweet soft voice with a warm low-mid register for a girl, very polite and humble, "
         "tender and caring, subtle breathy tone, slow and deliberate, Japanese anime heroine dubbed in Russian.",
    "g": "Young woman, light airy voice, soft-spoken and serene, faint melancholy, sincere and loyal, "
         "careful polite intonation rising softly at phrase ends, anime style.",
    "h": "Cheerful yet modest girl, clear bright high voice with a soft attack, eager to help, "
         "kind and loving, smooth gentle melody, Japanese anime idol voice acting.",
}
NUMBERS = {"1 минуту": "одну минуту", "2 минуты": "две минуты", "3 минуты": "три минуты",
           "1 час": "один час", "2 часа": "два часа"}
WORDS = {"5": "пять", "10": "десять", "15": "пятнадцать", "20": "двадцать", "25": "двадцать пять",
         "30": "тридцать", "40": "сорок", "45": "сорок пять"}


def spoken(text: str) -> str:
    """Цифры словами — так модель не ошибётся с падежом."""
    for a, b in NUMBERS.items():
        text = re.sub(rf"\b{a}\b", b, text)
    return re.sub(r"\b\d+\b", lambda m: WORDS[m.group()], text)


def trim(wav: np.ndarray, sr: int) -> np.ndarray:
    """Тишина по краям — 50 мс."""
    loud = np.flatnonzero(np.abs(wav) > 0.01)
    if not len(loud):
        return wav
    pad = int(0.05 * sr)
    return wav[max(0, loud[0] - pad): loud[-1] + pad]


def load(name: str):
    from qwen_tts import Qwen3TTSModel
    t = time.time()
    m = Qwen3TTSModel.from_pretrained(f"Qwen/{name}", device_map="cpu", dtype=torch.float32)
    print(f"{name}: загружена за {time.time() - t:.0f} с", flush=True)
    return m


def design(model, variant: str, seed: int):
    torch.manual_seed(seed)
    t = time.time()
    wavs, sr = model.generate_voice_design(text=REF_TEXT, language="Russian", instruct=VARIANTS[variant])
    print(f"{variant}{seed}: {len(wavs[0]) / sr:.1f} с звука за {time.time() - t:.0f} с", flush=True)
    return trim(wavs[0], sr), sr


def vox_samples(out: Path) -> None:
    """VoxCPM2: описание в скобках перед текстом, на выходе 48 кГц."""
    from voxcpm import VoxCPM
    t = time.time()
    m = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
    print(f"VoxCPM2: загружена за {time.time() - t:.0f} с", flush=True)
    for v, desc in VARIANTS.items():
        for seed in (1, 2):
            t = time.time()
            torch.manual_seed(seed)                 # у pip-версии voxcpm нет параметра seed
            wav = m.generate(text=f"({desc}){REF_TEXT}", cfg_value=2.0, inference_timesteps=10)
            sr = m.tts_model.sample_rate
            sf.write(out / f"vox-{v}{seed}.wav", trim(np.asarray(wav), sr), sr, subtype="PCM_16")
            print(f"vox-{v}{seed}: {len(wav) / sr:.1f} с звука за {time.time() - t:.0f} с", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["samples", "pack"])
    ap.add_argument("out", type=Path)
    ap.add_argument("--variant", default="a", choices=list(VARIANTS))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--engine", default="qwen", choices=["qwen", "vox"])
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    if a.engine == "vox":
        vox_samples(a.out)
        return 0
    dm = load("Qwen3-TTS-12Hz-1.7B-VoiceDesign")
    if a.mode == "samples":
        for v in VARIANTS:
            for seed in (1, 2):
                wav, sr = design(dm, v, seed)
                sf.write(a.out / f"qwen-{v}{seed}.wav", wav, sr, subtype="PCM_16")
        return 0

    ref, sr = design(dm, a.variant, a.seed)
    sf.write(a.out / "reference.wav", ref, sr)
    del dm
    cm = load("Qwen3-TTS-12Hz-0.6B-Base")
    prompt = cm.create_voice_clone_prompt(ref_audio=(ref, sr), ref_text=REF_TEXT)
    (a.out / "clips").mkdir(exist_ok=True)
    torch.manual_seed(a.seed)
    for text in voiceclips.phrases():
        t = time.time()
        wavs, sr = cm.generate_voice_clone(text=spoken(text), language="Russian", voice_clone_prompt=prompt)
        sf.write(a.out / "clips" / f"{voiceclips.key(text)}.wav", trim(wavs[0], sr), sr, subtype="PCM_16")
        print(f"{time.time() - t:4.0f} с  {text}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
