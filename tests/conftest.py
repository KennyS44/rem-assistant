import pytest


@pytest.fixture(autouse=True)
def _own_app_dir(tmp_path, monkeypatch):
    """Журнал и выученные команды — во временную папку, а не в ~/.rem: иначе проверки
    запоминали бы фразы и влияли друг на друга."""
    import rem.assistant
    monkeypatch.setattr(rem.assistant, "app_dir", lambda: tmp_path)
