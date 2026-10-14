import json

import pytest

from telepeep import cli, project


@pytest.fixture
def tty(monkeypatch):
    def set_tty(value: bool):
        monkeypatch.setattr(cli, "interactive", lambda: value)
    return set_tty


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.parametrize("cmd", [["grant", "work:team"], ["allow", "work", "1"], ["list", "work"], ["login", "work"]])
def test_user_commands_need_a_terminal(catalog, tty, capsys, cmd):
    tty(False)
    code, _, err = run(capsys, *cmd)
    assert code == 3 and "interactive terminal" in err


def test_no_project_is_json(catalog, capsys):
    code, out, _ = run(capsys, "history", "team")
    assert code == 3 and json.loads(out)["error"] == "no_project"


def test_not_granted_fails_before_telegram(catalog, capsys):
    project.init()
    code, out, _ = run(capsys, "history", "work:team")
    assert code == 3 and json.loads(out)["error"] == "no_access"


def test_granted_but_logged_out(catalog, capsys):
    project.init()
    project.save_access(project.Access(grants=[project.Grant("personal", -1002)]), catalog)
    code, out, _ = run(capsys, "history", "personal:team")
    assert code == 4 and json.loads(out)["error"] == "not_logged_in"


def test_chats_lists_only_project_grants(catalog, capsys):
    project.init()
    project.save_access(project.Access(grants=[project.Grant("work", -1001)]), catalog)
    code, out, _ = run(capsys, "chats")
    body = json.loads(out)
    assert code == 0 and "_notice" in body and [c["ref"] for c in body["chats"]] == ["work:team"]


def test_grant_and_revoke(catalog, tty, capsys):
    tty(True)
    project.init()
    assert run(capsys, "grant", "work:team", "boss")[0] == 0
    assert [(g.account, g.chat) for g in project.load_access().grants] == [("work", -1001), ("work", 5)]
    assert run(capsys, "grant", "team")[0] == 2  # in the catalogs of two accounts
    assert run(capsys, "grant", "work:nope")[0] == 3  # not in the catalog
    assert run(capsys, "revoke", "work:-1001")[0] == 0
    assert [g.chat for g in project.load_access().grants] == [5]


def test_version(capsys):
    assert run(capsys, "version")[1].strip() == f"telepeep v{project.VERSION}"
