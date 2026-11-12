import pytest

from hptui import custom


@pytest.fixture(autouse=True)
def isolated_parsers(tmp_path, monkeypatch):
    """Tests never see (or write) the user's real custom parsers."""
    monkeypatch.setenv("HPTUI_PARSERS", str(tmp_path / "parsers"))
    custom.clear_cache()
    yield tmp_path / "parsers"
    custom.clear_cache()
