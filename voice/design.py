"""Голос Рем по текстовому описанию (Qwen3-TTS, Apache 2.0) — для разработчика, не входит в Рэм.

Ничьи записи не используются: голос создаётся только по описанию VARIANTS.
Модели на 1,7 млрд параметров нужно ~10 ГБ памяти, поэтому запускается в GitHub Actions
(.github/workflows/voice-design.yml), результат — архив с WAV.

    python voice/design.py samples out/            — по 2 образца каждого варианта описания
    python voice/design.py samples out/ --engine vox   — то же моделью VoxCPM2 (Apache 2.0)
    python voice/design.py round2 out/             — голос № 10 (voice/ref/rem10.wav): клон этого
          синтетического образца с разной разметкой ударений и короткими паузами
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


TEST_TEXT = ("Рэм слушает. Скажите, что нужно сделать, и Рэм всё сделает. Простите, Рэм не расслышала. "
             "Таймер на сорок пять минут закончился. Выключить компьютер? Скажите «да» или «нет».")
FLUENT = VARIANTS["e"].replace("calm pace", "natural fluent pace without long pauses") + \
    " Native Russian pronunciation with correct word stress."
VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"


def stressed(text: str, mode: str, acc) -> str:
    """Ударения от ruaccent («скаж+ите»): plain — без них, acute — знак ударения над гласной,
    upper — ударная гласная заглавной, plus — как есть."""
    if mode == "plain":
        return text
    t = acc.process_all(text)
    if mode == "plus":
        return t
    if mode == "acute":
        return re.sub(rf"\+([{VOWELS}])", "\\1\u0301", t)
    return re.sub(rf"\+([{VOWELS}])", lambda m: m.group(1).upper(), t)


def short_pauses(wav: np.ndarray, sr: int, cap: float = 0.28) -> np.ndarray:
    """Паузы длиннее cap секунд укорачиваем до cap."""
    n = int(sr * 0.02)
    env = np.convolve(np.abs(wav), np.ones(n) / n, "same")
    quiet = env < 0.015
    out, i, keep = [], 0, int(cap * sr)
    while i < len(wav):
        j = i
        while j < len(wav) and quiet[j] == quiet[i]:
            j += 1
        seg = wav[i:j]
        if quiet[i] and len(seg) > keep:
            seg = np.concatenate([seg[:keep // 2], seg[-(keep // 2):]])
        out.append(seg)
        i = j
    return np.concatenate(out)


def round2(out: Path) -> None:
    from ruaccent import RUAccent
    acc = RUAccent()
    acc.load(omograph_model_size="turbo3.1", use_dictionary=True)
    ref = str(Path(__file__).parent / "ref" / "rem10.wav")

    def save(name, wav, sr):
        sf.write(out / f"{name}.wav", short_pauses(trim(np.asarray(wav), sr), sr), sr, subtype="PCM_16")
        print(name, flush=True)

    w, sr = sf.read(ref)
    save("r0-original", w, sr)                              # № 10 как был, только паузы короче
    for size, modes in (("1.7B", ("plain", "acute", "upper", "plus")), ("0.6B", ("plain", "acute"))):
        m = load(f"Qwen3-TTS-12Hz-{size}-Base")
        prompt = m.create_voice_clone_prompt(ref_audio=ref, ref_text=REF_TEXT)
        for mode in modes:
            torch.manual_seed(1)
            wavs, sr = m.generate_voice_clone(text=stressed(TEST_TEXT, mode, acc), language="Russian",
                                              voice_clone_prompt=prompt)
            save(f"r-clone{size}-{mode}", wavs[0], sr)
        del m
    dm = load("Qwen3-TTS-12Hz-1.7B-VoiceDesign")
    for seed in (1, 2):
        for mode in ("plain", "acute"):
            torch.manual_seed(seed)
            wavs, sr = dm.generate_voice_design(text=stressed(TEST_TEXT, mode, acc), language="Russian",
                                                instruct=FLUENT)
            save(f"r-fluent{seed}-{mode}", wavs[0], sr)


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
    ap.add_argument("mode", choices=["samples", "round2", "pack"])
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
    if a.mode == "round2":
        round2(a.out)
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
