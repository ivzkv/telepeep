import pytest

from telepeep import guard
from telepeep.errors import TelepeepError
from telepeep.project import Access, Grant


def test_resolves_by_name_id_and_prefix(catalog):
    access = Access(grants=[Grant("work", -1001), Grant("work", 5)])
    assert guard.resolve("team", access, catalog).ref == "work:team"
    assert guard.resolve("-1001", access, catalog).chat.id == -1001
    assert guard.resolve("work:boss", access, catalog).chat.id == 5


def test_not_granted(catalog):
    with pytest.raises(TelepeepError) as e:
        guard.resolve("personal:team", Access(grants=[Grant("work", -1001)]), catalog)
    assert e.value.code == "no_access"


def test_catalog_is_the_upper_bound(catalog):
    # Granted in access.toml but not in the account's catalog: editing the project file does not open a chat
    with pytest.raises(TelepeepError) as e:
        guard.resolve("work:-999", Access(grants=[Grant("work", -999)]), catalog)
    assert e.value.code == "no_access" and "catalog" in e.value.message


def test_unknown_account(catalog):
    with pytest.raises(TelepeepError) as e:
        guard.resolve("ghost:1", Access(grants=[Grant("ghost", 1)]), catalog)
    assert e.value.code == "no_access"


def test_ambiguous_name_and_default_account(catalog):
    grants = [Grant("work", -1001), Grant("personal", -1002)]
    with pytest.raises(TelepeepError) as e:
        guard.resolve("team", Access(grants=grants), catalog)
    assert e.value.code == "ambiguous_chat"
    assert guard.resolve("team", Access(default_account="personal", grants=grants), catalog).chat.id == -1002


def test_effective_statuses(catalog):
    access = Access(grants=[Grant("work", -1001), Grant("personal", -1002), Grant("work", -999), Grant("ghost", 1)])
    assert [c["status"] for c in guard.effective(access, catalog)] == \
        ["ok", "not_logged_in", "not_in_catalog", "unknown_account"]
