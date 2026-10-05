"""icon.svg → packaging/rem.ico, rem/art/icon.png, docs/icon.png.

Запускать после правки icon.svg (нужен Playwright с Chromium):  python packaging/art/make_icons.py
"""
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ART = Path(__file__).parent
ROOT = ART.parent.parent


def render(size: int) -> Image.Image:
    svg = (ART / "icon.svg").read_text(encoding="utf-8")
    svg = svg.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
    out = ART / "_render.png"
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": size, "height": size})
        page.set_content(f'<body style="margin:0;background:transparent">{svg}</body>')
        page.screenshot(path=str(out), omit_background=True)
        b.close()
    img = Image.open(out).convert("RGBA")
    out.unlink()
    return img


big = render(1024)
icon = big.resize((256, 256), Image.LANCZOS)
icon.save(ROOT / "rem" / "art" / "icon.png", optimize=True)
big.resize((128, 128), Image.LANCZOS).save(ROOT / "docs" / "icon.png", optimize=True)
big.resize((180, 180), Image.LANCZOS).save(ROOT / "docs" / "apple-touch-icon.png", optimize=True)
icon.save(ROOT / "packaging" / "rem.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
print("готово")
