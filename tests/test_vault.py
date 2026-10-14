import base64
import os
import sqlite3

import keyring
import pytest
from keyring.backends import fail

from telepeep import config, vault
from telepeep.errors import TelepeepError

AUTH_KEY = bytes(range(256))
DATA = {"dc_id": 2, "server_address": "149.154.167.51", "port": 443,
        "auth_key": base64.b64encode(AUTH_KEY).decode(), "takeout_id": None,
        "entities": [[-1001, 77, "team", None, "Team"], [5, 88, None, "380000000000", "Boss"]]}


def test_encrypted_roundtrip(home, secret_store):
    vault.write("work", DATA, "keyring")
    raw = vault.path("work").read_bytes()
    assert raw.startswith(vault.MAGIC_ENCRYPTED)
    assert AUTH_KEY not in raw and b"380000000000" not in raw and DATA["auth_key"].encode() not in raw
    assert ("telepeep", "work") in secret_store.items
    vault._keys.clear()  # a new process: the key comes from the secret store again
    assert vault.read("work", "keyring") == DATA


def test_no_session(home):
    assert vault.read("work", "keyring") is None and not vault.exists("work")


def test_lost_key(home, secret_store):
    vault.write("work", DATA, "keyring")
    secret_store.items.clear()
    vault._keys.clear()
    with pytest.raises(TelepeepError) as e:
        vault.read("work", "keyring")
    assert e.value.code == "not_logged_in" and "secret store" in e.value.message


def test_file_is_bound_to_its_account(home):
    vault.write("work", DATA, "keyring")
    vault.write("personal", DATA, "keyring")
    vault.path("work").replace(vault.path("personal"))  # someone swaps the files
    with pytest.raises(TelepeepError):
        vault.read("personal", "keyring")


def test_plain_storage_and_switching(home, secret_store):
    vault.write("work", DATA, "file")
    assert vault.path("work").read_bytes().startswith(vault.MAGIC_PLAIN) and not secret_store.items
    assert vault.read("work", "keyring") == DATA  # a file in the other format is still read
    vault.write("work", DATA, "keyring")
    assert vault.read("work", "file") == DATA


def test_no_secret_store(home):
    keyring.set_keyring(fail.Keyring())
    with pytest.raises(TelepeepError) as e:
        vault.write("work", DATA, "keyring")
    assert e.value.code == "no_secret_store" and 'storage = "file"' in e.value.hint
    vault.write("work", DATA, "file")  # the opt-out works without one
    assert vault.read("work", "file") == DATA


def test_delete_removes_file_and_key(home, secret_store):
    vault.write("work", DATA, "keyring")
    vault.delete("work")
    assert not vault.exists("work") and not secret_store.items
    vault.delete("work")  # nothing left: no error


def test_alias_is_checked(home):
    with pytest.raises(TelepeepError):
        vault.path("../evil")


def test_legacy_sqlite_session_is_converted(home):
    legacy = vault.legacy_path("work")
    legacy.parent.mkdir(parents=True)
    db = sqlite3.connect(legacy)
    db.execute("create table sessions (dc_id integer primary key, server_address text, port integer, auth_key blob, "
               "takeout_id integer, tmp_auth_key blob)")
    db.execute("create table entities (id integer primary key, hash integer not null, username text, phone integer, "
               "name text, date integer)")
    db.execute("insert into sessions values (2, '149.154.167.51', 443, ?, null, null)", (AUTH_KEY,))
    db.execute("insert into entities values (-1001, 77, 'team', null, 'Team', 0)")
    db.commit()
    db.close()

    data = vault.read("work", "keyring")
    assert data["auth_key"] == DATA["auth_key"] and data["entities"] == [[-1001, 77, "team", None, "Team"]]
    assert not legacy.exists() and vault.path("work").read_bytes().startswith(vault.MAGIC_ENCRYPTED)
    assert vault.read("work", "keyring") == data


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_private_files(home):
    vault.write("work", DATA, "keyring")
    config.save(config.Config(api_id=1, api_hash="secret"))
    assert vault.path("work").stat().st_mode & 0o777 == 0o600
    assert config.SESSIONS_DIR.stat().st_mode & 0o777 == 0o700
    assert config.CONFIG_FILE.stat().st_mode & 0o777 == 0o600


def test_telethon_session_roundtrip(home):
    from telethon.crypto import AuthKey
    from telethon.sessions import MemorySession
    from telethon.tl.types import InputPeerChannel

    from telepeep.tg import VaultSession

    s = VaultSession("work", "keyring")
    s.set_dc(2, "149.154.167.51", 443)
    s.auth_key = AuthKey(AUTH_KEY)
    s._entities = {(-1001234567890, 77, "team", None, "Team")}
    s.close()

    again = VaultSession("work", "keyring")
    assert (again.dc_id, again.server_address, again.port) == (2, "149.154.167.51", 443)
    assert again.auth_key.key == AUTH_KEY
    assert again.get_input_entity(-1001234567890) == InputPeerChannel(1234567890, 77)
    assert type(again.clone()) is MemorySession  # sessions for other data centers stay in memory
    again.delete()
    assert not vault.exists("work")
