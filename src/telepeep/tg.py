"""The only module that talks to Telegram and the only one that imports Telethon.

Read-only facade: dialogs, history, search, one message, media download — nothing that sends, edits, deletes,
forwards, pins, joins or marks as read (tests/test_readonly.py checks this file). It also avoids side effects other
people could see: updates are not received, read acknowledgements and online status are never sent.
`login` / `logout` are the exception by nature: they create and end the session itself.

Fails fast: Telethon's automatic flood-wait sleeping and retries are off, so an agent in a loop cannot dig the
account deeper into limits. Telegram errors become TelepeepError here; the rest of the package knows nothing of Telethon.
"""

from __future__ import annotations

import asyncio
import functools
import getpass
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from telethon import TelegramClient, errors, utils
from telethon.tl import types

from . import __version__, config
from .config import Account, Config
from .errors import EXIT_NOT_READY, EXIT_TELEGRAM, TelepeepError
from .project import RUN

SESSION_GONE = (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.SessionExpiredError,
                errors.AuthKeyDuplicatedError, errors.UserDeactivatedError, errors.UserDeactivatedBanError)
TELEGRAM_DENIED = (errors.ChannelPrivateError, errors.ChatAdminRequiredError, errors.ChannelInvalidError,
                   errors.PeerIdInvalidError)


def not_logged_in(alias: str, why: str) -> TelepeepError:
    return TelepeepError("not_logged_in", f"account '{alias}': {why}",
                         hint=f"the user runs `{RUN} login {alias}` in their terminal", exit_code=EXIT_NOT_READY)


def _translated(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except TelepeepError:
            raise
        except errors.FloodWaitError as e:
            raise TelepeepError("flood_wait", f"Telegram asks to wait {e.seconds} s before the next request",
                                hint="do not retry now; tell the user", exit_code=EXIT_TELEGRAM, seconds=e.seconds) from None
        except SESSION_GONE as e:
            raise TelepeepError("not_logged_in", f"the session is no longer valid ({type(e).__name__})",
                                hint=f"the user runs `{RUN} login <account>` in their terminal",
                                exit_code=EXIT_NOT_READY) from None
        except TELEGRAM_DENIED as e:
            raise TelepeepError("telegram_denied", f"Telegram refused access to the chat ({type(e).__name__}): "
                                "the account may have left it or been removed", exit_code=EXIT_TELEGRAM) from None
        except errors.RPCError as e:
            raise TelepeepError("telegram_error", f"Telegram error {type(e).__name__}: {e}", exit_code=EXIT_TELEGRAM) from None
        except (OSError, TimeoutError) as e:
            raise TelepeepError("network", f"cannot reach Telegram: {e or type(e).__name__}",
                                exit_code=EXIT_TELEGRAM) from None
    return wrapper


def _client(session: Path, api_id: int, api_hash: str, *, receive_updates: bool = False) -> TelegramClient:
    return TelegramClient(
        str(session), api_id, api_hash,
        # No update loop when reading: nothing to react to. QR login needs it: the scan arrives as an update.
        receive_updates=receive_updates,
        flood_sleep_threshold=0,  # FloodWait → error at once, never a silent sleep
        request_retries=1, connection_retries=2,
        device_model="telepeep", app_version=__version__,  # how the session shows in Settings → Devices
    )


# ---------- serialization ----------

def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def chat_kind(entity) -> str:
    if isinstance(entity, types.User):
        return "bot" if entity.bot else "user"
    if isinstance(entity, types.Chat):
        return "group"
    if isinstance(entity, types.Channel):
        return "channel" if entity.broadcast else "supergroup"
    return type(entity).__name__.lower()


def _media(m) -> dict | None:
    if m.media is None:
        return None
    if m.web_preview:
        out = {"type": "webpage", "url": m.web_preview.url}
    elif m.photo:
        out = {"type": "photo"}
    elif m.document:
        kind = next((k for k in ("voice", "video_note", "video", "gif", "sticker", "audio") if getattr(m, k)), "document")
        out = {"type": kind}
    elif m.geo:
        out = {"type": "geo", "lat": m.geo.lat, "long": m.geo.long}
    elif m.contact:
        out = {"type": "contact"}
    elif m.poll:
        out = {"type": "poll"}
    else:
        out = {"type": type(m.media).__name__.removeprefix("MessageMedia").lower()}
    if m.file is not None:  # downloadable with `media`
        out.update(downloadable=True, size=m.file.size, mime=m.file.mime_type)
        if m.file.name:
            out["file_name"] = m.file.name
    return out


def _message(m) -> dict:
    d: dict = {"id": m.id, "date": _iso(m.date)}
    if m.edit_date:
        d["edited"] = _iso(m.edit_date)
    if m.sender_id is not None:
        d["from"] = {"id": m.sender_id, "name": utils.get_display_name(m.sender) if m.sender else None}
    if m.out:
        d["out"] = True  # sent by the account itself
    if m.reply_to_msg_id:
        d["reply_to"] = m.reply_to_msg_id
    if m.fwd_from:
        fwd = m.fwd_from
        d["forwarded"] = {"from_id": utils.get_peer_id(fwd.from_id) if fwd.from_id else None,
                          "from_name": fwd.from_name, "date": _iso(fwd.date)}
    if m.grouped_id:
        d["album"] = m.grouped_id
    if m.action:
        d["service"] = type(m.action).__name__.removeprefix("MessageAction")
    if m.message:
        d["text"] = m.message
    media = _media(m)
    if media:
        d["media"] = media
    return d


# ---------- the session ----------

class Telegram:
    """`async with Telegram(cfg, account) as tg:` — connected, authorized, and the session belongs to account.user_id."""

    def __init__(self, cfg: Config, account: Account):
        self.account = account
        self.session = config.session_path(account.alias)
        # Checked before creating the client: Telethon creates an empty session file on construction
        if not self.session.exists():
            raise not_logged_in(account.alias, "no session on this machine")
        self.client = _client(self.session, *cfg.require_api())
        self._messages: dict = {}

    @_translated
    async def __aenter__(self) -> Telegram:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise not_logged_in(self.account.alias, "the session is not authorized")
        me = await self.client.get_me(input_peer=True)
        if self.account.user_id is not None and me.user_id != self.account.user_id:
            await self.client.disconnect()
            raise TelepeepError("session_mismatch", f"account '{self.account.alias}': the session belongs to user "
                                f"{me.user_id}, not {self.account.user_id}",
                                hint=f"the user runs `{RUN} login {self.account.alias}` in their terminal",
                                exit_code=EXIT_NOT_READY)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.client.disconnect()

    async def _peer(self, chat_id: int):
        try:
            peer = await self.client.get_input_entity(chat_id)
        except ValueError:
            # Not in the session's entity cache yet (e.g. right after login): one pass over the dialogs fills it
            async for _ in self.client.iter_dialogs():
                pass
            try:
                peer = await self.client.get_input_entity(chat_id)
            except ValueError:
                raise TelepeepError("chat_not_found", f"account '{self.account.alias}' does not see chat {chat_id}",
                                    exit_code=EXIT_TELEGRAM) from None
        if utils.get_peer_id(peer) != chat_id:
            raise TelepeepError("chat_not_found", f"chat {chat_id} resolved to another peer", exit_code=EXIT_TELEGRAM)
        return peer

    @_translated
    async def me(self) -> dict:
        me = await self.client.get_me()
        return {"user_id": me.id, "name": utils.get_display_name(me), "username": me.username, "bot": bool(me.bot)}

    @_translated
    async def dialogs(self) -> list[dict]:
        return [{"id": d.id, "type": chat_kind(d.entity), "title": d.name,
                 "username": getattr(d.entity, "username", None)}
                async for d in self.client.iter_dialogs()]

    @_translated
    async def describe(self, key: str) -> dict:
        """A chat for `allow`: numeric id, @username or t.me link → its id, type and title, as this account sees it."""
        key = key.strip()
        if key.lstrip("-").isdigit():
            entity = await self.client.get_entity(await self._peer(int(key)))
        else:
            try:
                entity = await self.client.get_entity(key)
            except ValueError:
                raise TelepeepError("chat_not_found", f"no chat '{key}' for account '{self.account.alias}'",
                                    exit_code=EXIT_TELEGRAM) from None
        return {"id": utils.get_peer_id(entity), "type": chat_kind(entity), "title": utils.get_display_name(entity),
                "username": getattr(entity, "username", None)}

    @_translated
    async def history(self, chat_id: int, *, limit: int, since: datetime | None = None,
                      after_id: int | None = None, before_id: int | None = None) -> tuple[list[dict], dict | None]:
        """Messages in ascending order, plus how to get the next page (`more`) when the page is full."""
        peer = await self._peer(chat_id)
        if after_id is not None or since is not None:
            # Oldest first, from a message id or a date
            kw = {"offset_id": after_id} if after_id is not None else {"offset_date": since}
            msgs = [m async for m in self.client.iter_messages(peer, limit=limit, reverse=True, **kw)]
            more = {"after_id": msgs[-1].id} if len(msgs) == limit else None
        else:
            # The latest ones, or older than a message id
            msgs = [m async for m in self.client.iter_messages(peer, limit=limit, offset_id=before_id or 0)]
            msgs.reverse()
            more = {"before_id": msgs[0].id} if len(msgs) == limit else None
        return [_message(m) for m in msgs], more

    @_translated
    async def search(self, chat_id: int, query: str, *, limit: int, since: datetime | None = None,
                     before_id: int | None = None) -> tuple[list[dict], dict | None]:
        """Telegram's server-side search in one chat, newest first until limit or since; returned in ascending order."""
        peer = await self._peer(chat_id)
        msgs, reached_since = [], False
        async for m in self.client.iter_messages(peer, search=query, offset_id=before_id or 0):
            if since is not None and m.date < since:
                reached_since = True
                break
            msgs.append(m)
            if len(msgs) == limit:
                break
        msgs.reverse()
        more = {"before_id": msgs[0].id} if len(msgs) == limit and not reached_since else None
        return [_message(m) for m in msgs], more

    async def _message_obj(self, chat_id: int, msg_id: int):
        if (chat_id, msg_id) not in self._messages:
            m = await self.client.get_messages(await self._peer(chat_id), ids=msg_id)
            # For users and basic groups message ids are account-wide: make sure it belongs to the granted chat
            if m is None or isinstance(m, types.MessageEmpty) or utils.get_peer_id(m.peer_id) != chat_id:
                raise TelepeepError("message_not_found", f"no message {msg_id} in chat {chat_id}", exit_code=EXIT_TELEGRAM)
            self._messages[(chat_id, msg_id)] = m
        return self._messages[(chat_id, msg_id)]

    @_translated
    async def attachment(self, chat_id: int, msg_id: int) -> dict:
        m = await self._message_obj(chat_id, msg_id)
        if m.file is None:
            raise TelepeepError("no_media", f"message {msg_id} has no downloadable attachment", exit_code=EXIT_TELEGRAM)
        return {"size": m.file.size, "mime": m.file.mime_type, "ext": m.file.ext, "file_name": m.file.name,
                "message": _message(m)}

    @_translated
    async def download(self, chat_id: int, msg_id: int, dest: Path) -> None:
        m = await self._message_obj(chat_id, msg_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            # A file object, not a path: Telethon then cannot add a name or extension of its own
            with open(tmp, "wb") as f:
                await self.client.download_media(m, file=f)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)


# ---------- login / logout (interactive, run by the user) ----------

# Where Telegram sent the login code. Some ways are for official apps only: Telethon cannot complete them.
CODE_SENT_TO = {
    types.auth.SentCodeTypeApp: "as a message in the Telegram app on your other devices (chat «Telegram»)",
    types.auth.SentCodeTypeSms: "by SMS",
    types.auth.SentCodeTypeSmsWord: "by SMS (a word)",
    types.auth.SentCodeTypeSmsPhrase: "by SMS (a phrase)",
    types.auth.SentCodeTypeCall: "by a phone call",
    types.auth.SentCodeTypeFlashCall: "by a flash call (the code is the caller's number)",
    types.auth.SentCodeTypeMissedCall: "by a missed call (the code is the last digits of the caller's number)",
    types.auth.SentCodeTypeEmailCode: "to your login email",
    types.auth.SentCodeTypeFragmentSms: "via Fragment (anonymous number)",
}
CODE_UNSUPPORTED = {
    types.auth.SentCodeTypeSetUpEmailRequired: "Telegram wants a login email set up first, which only official apps can do",
    types.auth.SentCodeTypeFirebaseSms: "Telegram sends the code only through official apps (Firebase)",
}


async def _password(client: TelegramClient) -> None:
    await client.sign_in(password=getpass.getpass("Two-step verification password (not shown): "))


async def _login_qr(client: TelegramClient, show_qr: Callable[[str], None]) -> None:
    qr = await client.qr_login()
    for attempt in range(3):  # a token lives ~30 s; three of them are enough to reach for the phone
        show_qr(qr.url)
        try:
            await qr.wait()
            return
        except asyncio.TimeoutError:
            if attempt < 2:
                await qr.recreate()
        except errors.SessionPasswordNeededError:
            await _password(client)
            return
    raise TelepeepError("qr_expired", "the QR code was not scanned in time", hint="run login again")


async def _login_phone(client: TelegramClient) -> None:
    phone = input("Phone number (international format, e.g. +380…): ").strip()
    sent = await client.send_code_request(phone)
    kind = type(sent.type)
    if kind in CODE_UNSUPPORTED:
        raise TelepeepError("code_unsupported", CODE_UNSUPPORTED[kind], hint="log in by QR code: login without --phone")
    where = CODE_SENT_TO.get(kind, f"in a way telepeep does not know ({kind.__name__})")
    if isinstance(sent.type, types.auth.SentCodeTypeEmailCode) and sent.type.email_pattern:
        where += f" ({sent.type.email_pattern})"
    print(f"Telegram sent the code {where}.")
    code = input("Code: ").strip()
    try:
        await client.sign_in(phone, code)
    except errors.SessionPasswordNeededError:
        await _password(client)


@_translated
async def login(cfg: Config, alias: str, *, use_phone: bool, show_qr: Callable[[str], None]) -> dict:
    """QR by default: scanned from the phone's Telegram, no code to deliver. --phone: the classic code flow."""
    session = config.session_path(alias)
    session.parent.mkdir(parents=True, exist_ok=True)
    client = _client(session, *cfg.require_api(), receive_updates=True)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await (_login_phone(client) if use_phone else _login_qr(client, show_qr))
        me = await client.get_me()
        if me.bot:
            await client.log_out()
            raise TelepeepError("bot_account", "telepeep reads as a user account, not a bot")
        return {"user_id": me.id, "name": utils.get_display_name(me), "username": me.username}
    finally:
        await client.disconnect()
        if os.name == "posix" and session.exists():
            session.chmod(0o600)


@_translated
async def logout(cfg: Config, alias: str) -> bool:
    """Ends the session on Telegram's side too (it disappears from Settings → Devices) and deletes the file."""
    session = config.session_path(alias)
    if not session.exists():
        return False
    client = _client(session, *cfg.require_api())
    try:
        await client.connect()
        if await client.is_user_authorized():
            await client.log_out()
    finally:
        await client.disconnect()
        session.unlink(missing_ok=True)
    return True
