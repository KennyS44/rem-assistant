from PIL import Image

from rem import look


def test_avatar_is_round_and_takes_top_of_tall_picture(tmp_path):
    p = tmp_path / "art.png"
    img = Image.new("RGB", (300, 600), "#202020")
    img.paste((230, 80, 120), (0, 0, 300, 300))          # «лицо» в верхней половине
    img.save(p)
    a = look.make_avatar(p)
    assert a.size == (256, 256)
    assert a.getpixel((0, 0))[3] == 0                    # углы прозрачные
    assert a.getpixel((128, 60))[:3] == (230, 80, 120)


def test_own_picture_replaces_icon(tmp_path, monkeypatch):
    monkeypatch.setattr(look, "avatar_path", lambda: tmp_path / "avatar.png")
    assert look.face().size == (256, 256) and not look.has_avatar()
    src = tmp_path / "x.png"
    Image.new("RGB", (64, 64), "red").save(src)
    look.set_avatar(src)
    assert look.face().getpixel((128, 128))[:3] == (255, 0, 0)
    look.clear_avatar()
    assert not look.has_avatar()


def test_tray_states():
    base = look.face()
    idle, paused = look.tray_image("idle", base), look.tray_image("paused", base)
    assert idle.size == (64, 64)
    assert idle.getpixel((50, 50)) != look.tray_image("listening", base).getpixel((50, 50))
    r, g, b, _ = paused.getpixel((32, 20))
    assert r == g == b                                   # на паузе — серый


def test_card_time():
    assert look.card_seconds("Да.") == 4
    assert look.card_seconds("слово " * 100) == 12
