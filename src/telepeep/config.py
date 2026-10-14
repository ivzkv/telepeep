"""Machine-wide state, shared by all projects of this OS user.

  <user config dir>/config.toml              api_id/api_hash, limits, accounts and the catalog of chats of each
  <user data dir>/sessions/<alias>.tps       session of an account — full access to it; encrypted (vault.py)

The catalog is the upper bound of what any project may read: a project grant (project.py) works only for a chat that is
in its account's catalog here. Paths come from platformdirs: %APPDATA% / %LOCALAPPDATA% on Windows, ~/.config and
~/.local/share on Linux, ~/Library/Application Support on macOS. There is deliberately no environment override: a
variable pointing to another config would let anyone who can run the tool replace the catalog.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs
import tomli_w

from . import RUN
from .errors import EXIT_NOT_READY, TelepeepError

CONFIG_DIR = Path(platformdirs.user_config_dir("telepeep", appauthor=False, roaming=True))
DATA_DIR = Path(platformdirs.user_data_dir("telepeep", appauthor=False))
CONFIG_FILE = CONFIG_DIR / "config.toml"
SESSIONS_DIR = DATA_DIR / "sessions"

DEFAULT_MAX_MEDIA_MB = 50
STORAGES = ("keyring", "file")  # where session keys live: the OS secret store, or none (unencrypted file)

# Account aliases and chat names: safe in file names, refs (`account:name`) and TOML keys without quoting
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

HEADER = "# telepeep: Telegram accounts and the chats each may expose to projects. Change with telepeep commands.\n\n"


@dataclass
class CatalogChat:
    id: int  # marked peer id: user > 0, basic group -N, supergroup/channel -100N
    name: str


@dataclass
class Account:
    alias: str
    user_id: int | None = None  # recorded at login; every connection checks that the session still belongs to it
    label: str = ""
    chats: list[CatalogChat] = field(default_factory=list)

    def chat_by_id(self, chat_id: int) -> CatalogChat | None:
        return next((c for c in self.chats if c.id == chat_id), None)

    def chat_by_name(self, name: str) -> CatalogChat | None:
        return next((c for c in self.chats if c.name == name), None)


@dataclass
class Config:
    api_id: int | None = None
    api_hash: str | None = None
    max_media_mb: int = DEFAULT_MAX_MEDIA_MB
    session_storage: str = "keyring"  # or "file"; see vault.py
    accounts: dict[str, Account] = field(default_factory=dict)

    def require_api(self) -> tuple[int, str]:
        if not self.api_id or not self.api_hash:
            raise TelepeepError(
                "not_configured", f"api_id / api_hash are not set in {CONFIG_FILE}",
                hint=f"the user runs `{RUN} login <account>` in their terminal", exit_code=EXIT_NOT_READY)
        return self.api_id, self.api_hash

    def account(self, alias: str) -> Account:
        acc = self.accounts.get(alias)
        if acc is None:
            known = ", ".join(self.accounts) or "none"
            raise TelepeepError("unknown_account", f"no account '{alias}' on this machine (known: {known})",
                                hint=f"the user runs `{RUN} login {alias}` in their terminal",
                                exit_code=EXIT_NOT_READY)
        return acc


def check_name(value: str, what: str) -> str:
    if not NAME_RE.match(value):
        raise TelepeepError("bad_name", f"{what} '{value}': use 1–32 characters a-z 0-9 _ -, starting with a letter or digit")
    return value


def _bad(message: str) -> TelepeepError:
    return TelepeepError("bad_config", f"{CONFIG_FILE}: {message}", hint="fix the file or recreate it with telepeep commands",
                         exit_code=EXIT_NOT_READY)


def _typed(value, kind, where: str):
    # bool is an int in Python; an id of `true` is a mistake, not 1
    if not isinstance(value, kind) or isinstance(value, bool):
        raise _bad(f"{where} must be {kind.__name__}")
    return value


def load() -> Config:
    if not CONFIG_FILE.exists():
        return Config()
    try:
        raw = tomllib.loads(CONFIG_FILE.read_text("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise _bad(str(e)) from None

    tg = raw.get("telegram", {})
    cfg = Config(
        api_id=_typed(tg["api_id"], int, "telegram.api_id") if "api_id" in tg else None,
        api_hash=_typed(tg["api_hash"], str, "telegram.api_hash") if "api_hash" in tg else None,
        max_media_mb=_typed(raw.get("limits", {}).get("max_media_mb", DEFAULT_MAX_MEDIA_MB), int, "limits.max_media_mb"),
        session_storage=_typed(raw.get("sessions", {}).get("storage", "keyring"), str, "sessions.storage"),
    )
    if cfg.session_storage not in STORAGES:
        raise _bad(f"sessions.storage must be one of: {', '.join(STORAGES)}")
    for alias, body in _typed(raw.get("accounts", {}), dict, "accounts").items():
        if not NAME_RE.match(alias):
            raise _bad(f"account alias '{alias}' is not valid")
        acc = Account(alias=alias,
                      user_id=_typed(body["user_id"], int, f"accounts.{alias}.user_id") if "user_id" in body else None,
                      label=_typed(body.get("label", ""), str, f"accounts.{alias}.label"))
        for i, chat in enumerate(_typed(body.get("chats", []), list, f"accounts.{alias}.chats")):
            where = f"accounts.{alias}.chats[{i}]"
            c = CatalogChat(id=_typed(chat.get("id"), int, f"{where}.id"), name=_typed(chat.get("name"), str, f"{where}.name"))
            if not NAME_RE.match(c.name):
                raise _bad(f"{where}.name '{c.name}' is not valid")
            if acc.chat_by_id(c.id) or acc.chat_by_name(c.name):
                raise _bad(f"{where}: id {c.id} or name '{c.name}' repeats in account '{alias}'")
            acc.chats.append(c)
        cfg.accounts[alias] = acc
    return cfg


def save(cfg: Config) -> None:
    raw: dict = {}
    if cfg.api_id or cfg.api_hash:
        raw["telegram"] = {k: v for k, v in (("api_id", cfg.api_id), ("api_hash", cfg.api_hash)) if v}
    raw["limits"] = {"max_media_mb": cfg.max_media_mb}
    raw["sessions"] = {"storage": cfg.session_storage}
    raw["accounts"] = {}
    for alias, acc in cfg.accounts.items():
        body: dict = {}
        if acc.user_id is not None:
            body["user_id"] = acc.user_id
        if acc.label:
            body["label"] = acc.label
        body["chats"] = [{"id": c.id, "name": c.name} for c in acc.chats]
        raw["accounts"][alias] = body
    write_atomic(CONFIG_FILE, HEADER + tomli_w.dumps(raw), private=True)  # holds api_hash


def write_atomic(path: Path, text: str, *, private: bool = False) -> None:
    write_bytes_atomic(path, text.encode("utf-8"), private=private)


def write_bytes_atomic(path: Path, data: bytes, *, private: bool = False) -> None:
    """A crash mid-write must not leave a half-written file. A private one (and its folder) is readable only by its
    owner from the start, not chmod-ed after writing. POSIX only: on Windows the user's profile ACL applies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if private and os.name == "posix":
        path.parent.chmod(0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)  # a leftover would keep its own mode
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600 if private else 0o666)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
