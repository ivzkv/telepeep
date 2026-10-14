"""Only tg.py talks to Telegram, and it calls nothing that writes."""

import ast
import re
from pathlib import Path

PKG = Path(__file__).parent.parent / "src" / "telepeep"
FORBIDDEN = re.compile(
    r"^(send_\w*|edit_\w*|delete_\w*|forward_\w*|pin_\w*|unpin_\w*|mark_\w*|kick_\w*|upload_\w*|"
    r"action|inline_query|.*read_acknowledge.*|join\w*|leave\w*|create_\w*|set_\w*|update_\w*)$")
ALLOWED = {"log_out", "send_code_request"}  # login/logout: creating and ending the session itself


def test_tg_calls_no_write_methods():
    tree = ast.parse((PKG / "tg.py").read_text("utf-8"))
    # Calls only: reading fields such as message.edit_date or message.action is fine
    attrs = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    bad = sorted(a for a in attrs if FORBIDDEN.match(a) and a not in ALLOWED)
    assert not bad, f"write-like calls in tg.py: {bad}"


def test_tg_sends_no_raw_requests():
    assert "functions" not in (PKG / "tg.py").read_text("utf-8"), "raw TL requests bypass the read-only facade"


def test_only_tg_imports_telethon():
    for f in PKG.glob("*.py"):
        if f.name == "tg.py":
            continue
        for n in ast.walk(ast.parse(f.read_text("utf-8"))):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            assert not any(m.split(".")[0] == "telethon" for m in names), f"{f.name} imports Telethon"
