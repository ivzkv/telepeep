import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from telepeep import config, vault


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if self.items.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def secret_store(monkeypatch):
    """Never the real OS secret store: an in-memory one, fresh for each test."""
    store = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(store)
    monkeypatch.setattr(vault, "_keys", {})
    yield store
    keyring.set_keyring(previous)


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
    vault.path("work").write_bytes(b"")
    return config.load()
