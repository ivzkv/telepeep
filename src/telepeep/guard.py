"""Access check, done on every call before anything goes to Telegram.

A chat is readable when all hold:
  1. the project grants the (account, chat id) pair      — .telepeep/access.toml
  2. the account's catalog on this machine lists the chat  — config.toml
  3. the account's session exists and belongs to the recorded user (checked on connect, tg.py)

A chat ref is `account:name`, `account:id`, or a bare name / id when the project's grants make it unambiguous.
It is resolved only among the project's grants: nothing is looked up in Telegram, so an unknown ref costs no request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import config
from .config import Account, CatalogChat, Config
from .errors import EXIT_USAGE, TelepeepError, no_access
from .project import RUN, Access


@dataclass(frozen=True)
class Target:
    account: Account
    chat: CatalogChat

    @property
    def ref(self) -> str:
        return f"{self.account.alias}:{self.chat.name}"

    def as_json(self) -> dict:
        return {"ref": self.ref, "account": self.account.alias, "id": self.chat.id, "name": self.chat.name}


def parse_ref(ref: str) -> tuple[str | None, str]:
    """`account:chat` → (account, chat); a bare `chat` → (None, chat)."""
    account, sep, chat = ref.partition(":")
    return (account, chat) if sep else (None, ref)


def as_chat_id(value: str) -> int | None:
    return int(value) if re.fullmatch(r"-?\d+", value) else None


def resolve(ref: str, access: Access, cfg: Config) -> Target:
    account_alias, key = parse_ref(ref.strip())
    chat_id = as_chat_id(key)

    matches = []
    for g in access.grants:
        if account_alias is not None and g.account != account_alias:
            continue
        account = cfg.accounts.get(g.account)
        cat = account.chat_by_id(g.chat) if account else None
        if (g.chat == chat_id) if chat_id is not None else (cat is not None and cat.name == key):
            matches.append((g, account, cat))

    if not matches:
        raise no_access(f"chat '{ref}' is not granted to this project",
                        hint=f"see `{RUN} chats` for what is granted; to grant more, the user runs "
                             f"`{RUN} grant <account>:<chat>` in their terminal")
    if len(matches) > 1 and access.default_account:
        matches = [m for m in matches if m[0].account == access.default_account] or matches
    if len(matches) > 1:
        options = ", ".join(f"{g.account}:{key}" for g, _, _ in matches)
        raise TelepeepError("ambiguous_chat", f"'{ref}' matches chats of several accounts: {options}",
                            hint="prefix the account", exit_code=EXIT_USAGE)

    grant, account, cat = matches[0]
    if account is None:
        raise no_access(f"account '{grant.account}' granted to this project does not exist on this machine",
                        hint=f"the user runs `{RUN} login {grant.account}` in their terminal")
    if cat is None:
        raise no_access(f"{grant.account}:{grant.chat} is granted to this project but is not in the account's catalog",
                        hint=f"the user runs `{RUN} allow {grant.account} {grant.chat}` in their terminal")
    return Target(account, cat)


def effective(access: Access, cfg: Config) -> list[dict]:
    """The project's grants with their state, for `chats`. Only `ok` ones can be read."""
    out = []
    for g in access.grants:
        account = cfg.accounts.get(g.account)
        cat = account.chat_by_id(g.chat) if account else None
        if account is None:
            status = "unknown_account"
        elif cat is None:
            status = "not_in_catalog"
        elif not config.session_path(g.account).exists():
            status = "not_logged_in"
        else:
            status = "ok"
        out.append({"ref": f"{g.account}:{cat.name if cat else g.chat}", "account": g.account, "id": g.chat,
                    "name": cat.name if cat else None, "status": status})
    return out
