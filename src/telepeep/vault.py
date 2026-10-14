"""Where an account's session is kept, and how.

A session is full access to the Telegram account, so by default it is encrypted on disk:

  <data dir>/sessions/<alias>.tps   AES-256-GCM ciphertext
  OS secret store, service "telepeep", entry <alias>   its key (Windows Credential Manager, macOS Keychain,
                                                       Secret Service on Linux)

A copied file, a backup or a synced folder is useless without the key. This does not stop a process running as the
same OS user: it can ask the secret store for the key just as telepeep does.

Where there is no secret store (headless Linux, WSL, containers), the user may set `storage = "file"` under
[sessions] in the config: the session is then kept unencrypted, readable only by its owner. Switching either way
takes effect on the next save; a file in the other format is still read.

The payload is what reading needs: data center, auth key, and the entity cache (access hashes, so granted chats resolve
without a request). Sessions of telepeep 0.1.0 (Telethon SQLite, <alias>.session) are converted on first use.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from pathlib import Path

from . import RUN, config
from .config import check_name, write_bytes_atomic
from .errors import EXIT_NOT_READY, TelepeepError

SERVICE = "telepeep"
MAGIC_ENCRYPTED = b"telepeep session 1 aes-256-gcm\n"
MAGIC_PLAIN = b"telepeep session 1 plain\n"
NONCE_SIZE = 12

_keys: dict[str, bytes] = {}  # per process: the secret store is asked once per account


def path(alias: str) -> Path:
    return config.SESSIONS_DIR / f"{check_name(alias, 'account alias')}.tps"


def legacy_path(alias: str) -> Path:
    return config.SESSIONS_DIR / f"{check_name(alias, 'account alias')}.session"


def exists(alias: str) -> bool:
    return path(alias).exists() or legacy_path(alias).exists()


def _unreadable(alias: str, why: str) -> TelepeepError:
    return TelepeepError("not_logged_in", f"account '{alias}': the session cannot be read ({why})",
                         hint=f"the user runs `{RUN} login {alias}` in their terminal", exit_code=EXIT_NOT_READY)


def _no_store(e: Exception) -> TelepeepError:
    return TelepeepError(
        "no_secret_store", f"no OS secret store for the session key ({e})",
        hint=f'on a machine without one (headless Linux, WSL, containers) the user may set storage = "file" under '
             f"[sessions] in {config.CONFIG_FILE}: sessions are then kept unencrypted, readable only by them",
        exit_code=EXIT_NOT_READY)


def _key(alias: str, *, create: bool) -> bytes | None:
    if alias in _keys:
        return _keys[alias]
    import keyring
    from keyring.errors import KeyringError
    try:
        stored = keyring.get_password(SERVICE, alias)
        if stored is None:
            if not create:
                return None
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            stored = base64.b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii")
            keyring.set_password(SERVICE, alias, stored)
    except KeyringError as e:
        raise _no_store(e) from None
    _keys[alias] = base64.b64decode(stored)
    return _keys[alias]


def _decrypt(alias: str, blob: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _key(alias, create=False)
    if key is None:
        raise _unreadable(alias, "its key is not in the OS secret store")
    try:
        # The alias is authenticated too: a file renamed to another account's name does not open
        return AESGCM(key).decrypt(blob[:NONCE_SIZE], blob[NONCE_SIZE:], alias.encode())
    except InvalidTag:
        raise _unreadable(alias, "it does not match the key in the OS secret store") from None


def read(alias: str, storage: str) -> dict | None:
    """The session's data, or None when the account has no session on this machine."""
    p = path(alias)
    if not p.exists():
        return _migrate(alias, storage)
    raw = p.read_bytes()
    if raw.startswith(MAGIC_ENCRYPTED):
        payload = _decrypt(alias, raw[len(MAGIC_ENCRYPTED):])
    elif raw.startswith(MAGIC_PLAIN):
        payload = raw[len(MAGIC_PLAIN):]
    else:
        raise _unreadable(alias, "unknown format")
    try:
        return json.loads(payload)
    except ValueError:
        raise _unreadable(alias, "damaged") from None


def write(alias: str, data: dict, storage: str) -> None:
    payload = json.dumps(data, separators=(",", ":")).encode()
    if storage == "file":
        blob = MAGIC_PLAIN + payload
    else:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(NONCE_SIZE)
        blob = MAGIC_ENCRYPTED + nonce + AESGCM(_key(alias, create=True)).encrypt(nonce, payload, alias.encode())
    write_bytes_atomic(path(alias), blob, private=True)


def delete(alias: str) -> None:
    """The session file in any format and its key."""
    for p in (path(alias), legacy_path(alias), legacy_path(alias).with_name(legacy_path(alias).name + "-journal")):
        p.unlink(missing_ok=True)
    _keys.pop(alias, None)
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError
    try:
        keyring.delete_password(SERVICE, alias)
    except (PasswordDeleteError, KeyringError):
        pass  # no key stored, or no secret store: nothing to delete


def _migrate(alias: str, storage: str) -> dict | None:
    """A 0.1.0 session (Telethon SQLite) → this format; the old file is deleted once the new one is written."""
    legacy = legacy_path(alias)
    if not legacy.exists():
        return None
    try:
        with sqlite3.connect(legacy) as db:
            row = db.execute("select dc_id, server_address, port, auth_key, takeout_id from sessions").fetchone()
            entities = db.execute("select id, hash, username, phone, name from entities").fetchall()
        db.close()
    except sqlite3.Error as e:
        raise _unreadable(alias, f"old session file: {e}") from None
    if row is None:
        raise _unreadable(alias, "old session file is empty")
    dc_id, server_address, port, auth_key, takeout_id = row
    data = {"dc_id": dc_id, "server_address": server_address, "port": port,
            "auth_key": base64.b64encode(auth_key).decode("ascii") if auth_key else None,
            "takeout_id": takeout_id, "entities": [list(e) for e in entities]}
    write(alias, data, storage)
    legacy.unlink()
    legacy.with_name(legacy.name + "-journal").unlink(missing_ok=True)
    return data
