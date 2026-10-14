import json

import pytest

from telepeep import config, project
from telepeep.errors import TelepeepError


def test_config_roundtrip(catalog):
    catalog.accounts["work"].label = 'Рабочий "аккаунт"'
    config.save(catalog)
    assert config.load() == catalog


def test_config_rejects_bad_names(home):
    config.CONFIG_DIR.mkdir(parents=True)
    config.CONFIG_FILE.write_text('[accounts.work]\nchats = [{ id = 1, name = "../x" }]\n', "utf-8")
    with pytest.raises(TelepeepError) as e:
        config.load()
    assert e.value.code == "bad_config"


def test_config_rejects_unknown_storage(home):
    config.CONFIG_DIR.mkdir(parents=True)
    config.CONFIG_FILE.write_text('[sessions]\nstorage = "cloud"\n', "utf-8")
    with pytest.raises(TelepeepError) as e:
        config.load()
    assert e.value.code == "bad_config"


def test_no_project(home):
    with pytest.raises(TelepeepError) as e:
        project.load_access()
    assert e.value.code == "no_project"


def test_init_and_access_roundtrip(catalog):
    assert any("Created" in line for line in project.init())
    assert project.pinned_version() == project.VERSION
    assert project.SKILL_DST.read_text("utf-8") == project.skill_text()
    assert "!version" in project.GITIGNORE_FILE.read_text("utf-8")

    access = project.load_access()
    access.default_account = "work"
    access.max_media_mb = 10
    access.grants = [project.Grant("work", -1001), project.Grant("personal", -1002)]
    project.save_access(access, catalog)
    assert project.load_access() == access
    assert "# work:team" in project.ACCESS_FILE.read_text("utf-8")


def test_gitignore_edit_is_kept(home):
    project.PROJECT_DIR.mkdir()
    project.GITIGNORE_FILE.write_text("custom\n", "utf-8")
    project.install_skill()
    assert project.GITIGNORE_FILE.read_text("utf-8") == "custom\n"


def test_deny_rules_reported_until_present(home):
    assert project.missing_deny_rules() == project.deny_rules()
    (home / "proj" / ".claude").mkdir()
    (home / "proj" / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"deny": project.deny_rules()}}), "utf-8")
    assert project.missing_deny_rules() == []
