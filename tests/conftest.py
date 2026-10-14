import pytest

from telepeep import config


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A machine config and a project folder of their own; the test runs from the project root."""
    cfg_dir, data_dir, proj = tmp_path / "cfg", tmp_path / "data", tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_dir / "config.toml")
    monkeypatch.setattr(config, "SESSIONS_DIR", data_dir / "sessions")
    monkeypatch.chdir(proj)
    return tmp_path


@pytest.fixture
def catalog(home):
    """Two accounts sharing a chat name; only `work` is logged in."""
    cfg = config.Config(api_id=1, api_hash="h", accounts={
        "work": config.Account("work", user_id=11, chats=[config.CatalogChat(-1001, "team"), config.CatalogChat(5, "boss")]),
        "personal": config.Account("personal", user_id=22, chats=[config.CatalogChat(-1002, "team")]),
    })
    config.save(cfg)
    config.SESSIONS_DIR.mkdir(parents=True)
    (config.SESSIONS_DIR / "work.session").write_bytes(b"")
    return config.load()
