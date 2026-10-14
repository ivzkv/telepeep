"""telepeep — read-only Telegram for coding agents.

Two kinds of commands:
  for the agent (JSON on stdout):  chats, history, search, media, status, init, skill, version
  for the user only:               login, logout, accounts, list, allow, deny, grant, revoke
The user-only ones change access or show the whole account; they run only with a terminal on stdin, which an agent's
shell does not have. This is a guard against a mistaken agent, not a security boundary: whoever can edit the files
can change access too (see the deny rules `skill` suggests).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from . import config, guard, media, project, vault
from .config import CONFIG_FILE, SESSIONS_DIR, check_name
from .errors import EXIT_NO_ACCESS, EXIT_USAGE, TelepeepError
from .guard import as_chat_id, parse_ref
from .output import data, emit
from .project import RUN, VERSION
from .tg import Telegram

MAX_LIMIT = 500


def _out(line: str = "") -> None:
    print(line)


def interactive() -> bool:
    """A person at a terminal. On Windows isatty() is also true for NUL — what agent shells often give as stdin — so
    there it must be a real console."""
    if not sys.stdin.isatty():
        return False
    if os.name != "nt":
        return True
    import ctypes
    import msvcrt
    mode = ctypes.c_uint32()
    return bool(ctypes.windll.kernel32.GetConsoleMode(msvcrt.get_osfhandle(sys.stdin.fileno()), ctypes.byref(mode)))


def _limit(value: str) -> int:
    n = int(value)
    if not 1 <= n <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"must be 1…{MAX_LIMIT}")
    return n


def _date(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD or an ISO date-time") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- for the agent ----------

def _target(ref: str):
    access = project.load_access()
    cfg = config.load()
    return guard.resolve(ref, access, cfg), cfg, access


def cmd_chats(args) -> None:
    emit(data(chats=guard.effective(project.load_access(), config.load())))


async def cmd_history(args) -> None:
    target, cfg, _ = _target(args.chat)
    async with Telegram(cfg, target.account) as tg:
        messages, more = await tg.history(target.chat.id, limit=args.limit, since=args.since,
                                          after_id=args.after_id, before_id=args.before_id)
    emit(data(chat=target.as_json(), messages=messages, more=more))


async def cmd_search(args) -> None:
    if not args.query.strip():
        raise TelepeepError("bad_query", "the search text is empty")
    target, cfg, _ = _target(args.chat)
    async with Telegram(cfg, target.account) as tg:
        messages, more = await tg.search(target.chat.id, args.query, limit=args.limit, since=args.since,
                                         before_id=args.before_id)
    emit(data(chat=target.as_json(), query=args.query, messages=messages, more=more))


async def cmd_media(args) -> None:
    target, cfg, access = _target(args.chat)
    limit_mb = media.limit_mb(cfg, access)
    async with Telegram(cfg, target.account) as tg:
        info = await tg.attachment(target.chat.id, args.msg_id)
        size = info["size"]
        if size is not None and size > limit_mb * 1024 * 1024:
            raise TelepeepError("too_large", f"the attachment is {size / 1024 / 1024:.1f} MB, the limit is {limit_mb} MB",
                                hint="the user can raise limits.max_media_mb in the telepeep config", exit_code=EXIT_NO_ACCESS)
        dest = media.target_path(target.account.alias, target.chat.id, args.msg_id, media.safe_ext(info["ext"], info["mime"]))
        cached = dest.exists() and size is not None and dest.stat().st_size == size
        if not cached:
            await tg.download(target.chat.id, args.msg_id, dest)
    emit(data(chat=target.as_json(), message=info["message"], path=dest.as_posix(), size=dest.stat().st_size,
              mime=info["mime"], file_name=info["file_name"], cached=cached))


async def cmd_status(args) -> None:
    state = project.setup_state()
    hints = []
    if state["pin"] == "missing":
        hints.append(f"not pinned: {RUN} skill")
    elif state["pin"] == "differs":
        hints.append(project.mismatch_hint(state["pinned"]))
    if state["skill"] != "matches":
        hints.append(f"skill {state['skill']}: {RUN} skill")
    missing = project.missing_deny_rules()
    if missing:
        hints.append("add to .claude/settings.json permissions.deny: " + ", ".join(missing))
    cfg = config.load()
    if cfg.session_storage == "file":
        hints.append("sessions are kept unencrypted (sessions.storage = \"file\" in the config)")

    result = {"version": VERSION, "project": state, "config_file": str(CONFIG_FILE), "config_found": CONFIG_FILE.exists(),
              "sessions_dir": str(SESSIONS_DIR), "session_storage": cfg.session_storage, "deny_rules_missing": missing}
    if state["access_file"]:
        access = project.load_access()
        result["chats"] = guard.effective(access, cfg)
        accounts = []
        for alias in dict.fromkeys(g.account for g in access.grants):
            entry = {"account": alias}
            try:
                async with Telegram(cfg, cfg.account(alias)) as tg:
                    me = await tg.me()
                entry.update(status="ok", name=me["name"])
            except TelepeepError as e:
                entry.update(status=e.code, message=e.message)
            accounts.append(entry)
        result["accounts"] = accounts
    else:
        hints.append(f"no project here: {RUN} init")
    result["hints"] = hints
    emit(result)


def cmd_init(args) -> None:
    for line in project.init():
        _out(line)
    _out(f"\nNext, in your terminal: {RUN} login <account>, {RUN} allow <account> <chat>, {RUN} grant <account>:<chat>")


def cmd_skill(args) -> None:
    for line in project.install_skill():
        _out(line)


def cmd_version(args) -> None:
    _out(f"telepeep v{VERSION}")


# ---------- for the user ----------

def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _show_qr(url: str) -> None:
    import qrcode
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    _out("\nOn your phone: Telegram → Settings → Devices → Link Desktop Device, and scan this code:\n")
    qr.print_ascii(invert=True)
    _out("Waiting for the scan… (the code renews itself; Ctrl+C to stop; --phone to log in with a code instead)")


def cmd_login(args) -> None:
    alias = check_name(args.account, "account alias")
    cfg = config.load()
    if not cfg.api_id or not cfg.api_hash:
        _out("Telegram API credentials are needed once per machine: my.telegram.org → API development tools.")
        api_id = _ask("api_id: ")
        if not api_id.isdigit():
            raise TelepeepError("bad_api_id", "api_id is a number")
        cfg.api_id, cfg.api_hash = int(api_id), _ask("api_hash: ")
        config.save(cfg)

    from . import tg
    me = asyncio.run(tg.login(cfg, alias, use_phone=args.phone, show_qr=_show_qr))
    account = cfg.accounts.get(alias)
    if account and account.user_id is not None and account.user_id != me["user_id"]:
        asyncio.run(tg.logout(cfg, alias))
        raise TelepeepError("session_mismatch", f"account '{alias}' is user {account.user_id}, but you logged in as "
                            f"{me['name']} ({me['user_id']}) — logged out again. Use another alias for this account.")
    if account is None:
        account = cfg.accounts[alias] = config.Account(alias)
    account.user_id = me["user_id"]
    if args.label is not None:
        account.label = args.label
    config.save(cfg)
    who = me["name"] + (f" (@{me['username']})" if me["username"] else "")
    kept = ("encrypted, key in the OS secret store" if cfg.session_storage == "keyring"
            else "unencrypted, readable only by you")
    _out(f"Logged in as {who} → account '{alias}'. Session: {vault.path(alias)} ({kept})")
    _out(f"Next: {RUN} list {alias}, then {RUN} allow {alias} <chat>")


def cmd_logout(args) -> None:
    from . import tg
    cfg = config.load()
    cfg.account(args.account)
    done = asyncio.run(tg.logout(cfg, args.account))
    _out(f"Account '{args.account}': " + ("logged out, session and its key deleted" if done else "no session on this machine")
         + ". Its catalog is kept; projects cannot read its chats until you log in again.")


def cmd_accounts(args) -> None:
    cfg = config.load()
    if not cfg.accounts:
        _out(f"No accounts yet: {RUN} login <account>")
    for acc in cfg.accounts.values():
        session = "logged in" if vault.exists(acc.alias) else "no session"
        _out(f"{acc.alias:<16} {session:<11} user {acc.user_id}  {len(acc.chats)} chat(s) in catalog  {acc.label}")
        for c in acc.chats:
            _out(f"    {c.name:<24} {c.id}")
    _out(f"\nConfig: {CONFIG_FILE}\nSessions: {SESSIONS_DIR}")


async def cmd_list(args) -> None:
    cfg = config.load()
    account = cfg.account(args.account)
    async with Telegram(cfg, account) as tg:
        dialogs = await tg.dialogs()
    needle = (args.filter or "").casefold()
    for d in dialogs:
        if needle and needle not in (d["title"] or "").casefold() and needle not in (d["username"] or "").casefold():
            continue
        cat = account.chat_by_id(d["id"])
        mark = f"[{cat.name}]" if cat else ""
        user = f"@{d['username']}" if d["username"] else ""
        _out(f"{d['id']:>16}  {d['type']:<10} {mark:<18} {d['title']}  {user}")


async def cmd_allow(args) -> None:
    cfg = config.load()
    account = cfg.account(args.account)
    async with Telegram(cfg, account) as tg:
        chat = await tg.describe(args.chat)
    existing = account.chat_by_id(chat["id"])
    suggestion = existing.name if existing else (chat["username"] or "").lower()
    if not config.NAME_RE.match(suggestion):
        suggestion = ""
    _out(f"{chat['type']} {chat['id']}: {chat['title']}")
    name = args.name or _ask(f"Name in the catalog (a-z 0-9 _ -){f' [{suggestion}]' if suggestion else ''}: ") or suggestion
    check_name(name, "chat name")
    other = account.chat_by_name(name)
    if other and other.id != chat["id"]:
        raise TelepeepError("name_taken", f"'{name}' is already chat {other.id} in account '{account.alias}'")
    if existing:
        existing.name = name
    else:
        account.chats.append(config.CatalogChat(chat["id"], name))
    config.save(cfg)
    _out(f"{account.alias}:{name} is in the catalog. Grant it to a project (from its root): {RUN} grant {account.alias}:{name}")


def cmd_deny(args) -> None:
    cfg = config.load()
    account = cfg.account(args.account)
    chat_id = as_chat_id(args.chat)
    cat = account.chat_by_id(chat_id) if chat_id is not None else account.chat_by_name(args.chat)
    if cat is None:
        raise TelepeepError("not_in_catalog", f"no '{args.chat}' in the catalog of '{account.alias}'")
    account.chats.remove(cat)
    config.save(cfg)
    _out(f"Removed {account.alias}:{cat.name} ({cat.id}) from the catalog. Projects that grant it can no longer read it;"
         " their grants stay and work again if you allow the chat again.")


def _catalog_lookup(ref: str, cfg: config.Config) -> tuple[str, config.CatalogChat]:
    alias, key = parse_ref(ref)
    chat_id = as_chat_id(key)
    accounts = [cfg.account(alias)] if alias else list(cfg.accounts.values())
    found = [(a.alias, c) for a in accounts
             for c in ([a.chat_by_id(chat_id)] if chat_id is not None else [a.chat_by_name(key)]) if c]
    if not found:
        raise TelepeepError("not_in_catalog", f"'{ref}' is not in the catalog",
                            hint=f"add it first: {RUN} allow <account> <chat>", exit_code=EXIT_NO_ACCESS)
    if len(found) > 1:
        raise TelepeepError("ambiguous_chat", f"'{ref}' is in several accounts: "
                            + ", ".join(f"{a}:{c.name}" for a, c in found), hint="prefix the account")
    return found[0]


def cmd_grant(args) -> None:
    access = project.load_access()
    cfg = config.load()
    for ref in args.refs:
        alias, cat = _catalog_lookup(ref, cfg)
        if access.has(alias, cat.id):
            _out(f"- {alias}:{cat.name} is already granted")
        else:
            access.grants.append(project.Grant(alias, cat.id))
            _out(f"- Granted {alias}:{cat.name} ({cat.id})")
    project.save_access(access, cfg)


def cmd_revoke(args) -> None:
    access = project.load_access()
    cfg = config.load()
    for ref in args.refs:
        alias, key = parse_ref(ref)
        chat_id = as_chat_id(key)
        before = len(access.grants)

        def matches(g: project.Grant) -> bool:
            if alias is not None and g.account != alias:
                return False
            if chat_id is not None:
                return g.chat == chat_id
            acc = cfg.accounts.get(g.account)
            cat = acc.chat_by_id(g.chat) if acc else None
            return cat is not None and cat.name == key

        access.grants = [g for g in access.grants if not matches(g)]
        _out(f"- {ref}: " + (f"revoked {before - len(access.grants)}" if len(access.grants) < before else "was not granted"))
    project.save_access(access, cfg)


# ---------- wiring ----------

USER_ONLY = {"login", "logout", "accounts", "list", "allow", "deny", "grant", "revoke"}
JSON_OUTPUT = {"chats", "history", "search", "media", "status"}
NO_PIN_WARNING = {"init", "skill", "version"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="telepeep", description="Read-only Telegram for coding agents.")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    add("chats", cmd_chats, "chats this project may read")
    sp = add("history", cmd_history, "messages of a chat, ascending")
    sp.add_argument("chat", help="account:name, account:id, or a name/id unambiguous in the project")
    sp.add_argument("--limit", type=_limit, default=50, help=f"1…{MAX_LIMIT}, default 50")
    order = sp.add_mutually_exclusive_group()
    order.add_argument("--since", type=_date, help="the first messages from this date (UTC unless given)")
    order.add_argument("--after-id", type=int, help="newer than this message id")
    order.add_argument("--before-id", type=int, help="older than this message id")
    sp = add("search", cmd_search, "Telegram's search within a chat")
    sp.add_argument("chat")
    sp.add_argument("query")
    sp.add_argument("--limit", type=_limit, default=50)
    sp.add_argument("--since", type=_date, help="only messages from this date")
    sp.add_argument("--before-id", type=int, help="older than this message id")
    sp = add("media", cmd_media, "download a message's attachment, print its path")
    sp.add_argument("chat")
    sp.add_argument("msg_id", type=int)
    add("status", cmd_status, "version, pin and skill, accounts and chats of this project")
    add("init", cmd_init, "set up the current folder as a project")
    add("skill", cmd_skill, "pin the project to this version and install its skill")
    add("version", cmd_version, "tool version")

    sp = add("login", cmd_login, "[user] log in to a Telegram account under an alias")
    sp.add_argument("account")
    sp.add_argument("--label", help="free text shown in `accounts`")
    sp.add_argument("--phone", action="store_true", help="log in with phone and code instead of a QR code")
    sp = add("logout", cmd_logout, "[user] end the account's session")
    sp.add_argument("account")
    add("accounts", cmd_accounts, "[user] accounts and their catalogs")
    sp = add("list", cmd_list, "[user] all chats of an account")
    sp.add_argument("account")
    sp.add_argument("--filter", help="substring of the title or username")
    sp = add("allow", cmd_allow, "[user] add a chat to the account's catalog")
    sp.add_argument("account")
    sp.add_argument("chat", help="numeric id, @username or t.me link")
    sp.add_argument("--name", help="name in the catalog")
    sp = add("deny", cmd_deny, "[user] remove a chat from the account's catalog")
    sp.add_argument("account")
    sp.add_argument("chat", help="numeric id or catalog name")
    sp = add("grant", cmd_grant, "[user] let this project read catalog chats")
    sp.add_argument("refs", nargs="+", metavar="account:chat")
    sp = add("revoke", cmd_revoke, "[user] take chats away from this project")
    sp.add_argument("refs", nargs="+", metavar="account:chat")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # Cyrillic etc. on a Windows console
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return EXIT_USAGE

    pinned = project.pinned_version()
    if pinned and pinned != VERSION and args.cmd not in NO_PIN_WARNING:
        print(f"⚠️ {project.VERSION_FILE.as_posix()} pins v{pinned}, but v{VERSION} is running — "
              f"{project.mismatch_hint(pinned)}", file=sys.stderr)
    try:
        if args.cmd in USER_ONLY and not interactive():
            raise TelepeepError("needs_terminal", f"`telepeep {args.cmd}` changes access or shows the whole account, so it "
                                "runs only in an interactive terminal",
                                hint=f"ask the user to run `{RUN} {args.cmd} …` in their own terminal",
                                exit_code=EXIT_NO_ACCESS)
        result = args.fn(args)
        if asyncio.iscoroutine(result):
            asyncio.run(result)
        return 0
    except TelepeepError as e:
        if args.cmd in JSON_OUTPUT:
            emit(e.as_json())
        else:
            print(f"⛔ {e.message}" + (f"\n   → {e.hint}" if e.hint else ""), file=sys.stderr)
        return e.exit_code
    except (KeyboardInterrupt, EOFError):
        print("\ninterrupted", file=sys.stderr)
        return 130
