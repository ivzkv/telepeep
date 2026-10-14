# telepeep

**Give your coding agent safe, read-only access to the Telegram chats you choose — per project.**

telepeep is a small CLI on [Telethon](https://github.com/LonamiWebs/Telethon) that reads Telegram as your own user
account and prints JSON an agent can parse. It ships a [Claude Code](https://claude.com/claude-code) skill and connects
to a project the same way as [atlasmd](https://github.com/ivzkv/atlasmd): the project commits a version pin and the
skill, and the agent fetches the tool itself.

## Why telepeep

- **Read-only by design.** No command sends, edits, deletes or forwards; read receipts and online status are never
  sent. Only one module imports Telethon, and a test checks that it calls nothing that writes.
- **You decide what each project sees.** Two levels: a machine-wide *catalog* of chats per account (the upper bound),
  and per-project *grants* from that catalog. A chat outside the catalog is unreachable from any project.
- **Several accounts.** Each is logged in under an alias (`work`, `personal`); a project may use any of them.
- **Checked on every call.** The chat is resolved among the project's grants and checked against the catalog before
  anything goes to Telegram — an unknown chat costs no request.
- **Fails fast.** Flood-wait is an error at once, not a silent sleep; the agent is told to stop, not to loop.
- **Safe file names.** Attachments are saved as `<msg_id>.<ext>` with the extension from a fixed list — nothing from
  Telegram reaches a path.
- **Data, not instructions.** Every output with Telegram content carries a `_notice` that it is untrusted data.

## Where things live

Machine-wide, per OS user (paths from [platformdirs](https://github.com/tox-dev/platformdirs)):

```
<config dir>/telepeep/config.toml        API credentials, limits, accounts and the catalog of each
<data dir>/telepeep/sessions/<alias>.tps       one session per account — full access to it; encrypted
```

A session file is encrypted (AES-256-GCM); its key is kept in the OS secret store: Windows Credential Manager,
macOS Keychain, Secret Service on Linux (GNOME Keyring, KWallet). Where there is none — headless Linux, WSL,
containers — set `storage = "file"` under `[sessions]` to keep sessions unencrypted, readable only by you.
Sessions from telepeep 0.1.0 (`<alias>.session`) are converted on first use.

`<config dir>` is `%APPDATA%` on Windows, `~/.config` on Linux, `~/Library/Application Support` on macOS;
`<data dir>` is `%LOCALAPPDATA%`, `~/.local/share`, `~/Library/Application Support`. `telepeep status` prints both.

In the project:

```
.telepeep/version       pinned tool version                          (committed)
.telepeep/.gitignore    keeps the rest out of git                    (committed)
.telepeep/access.toml   account/chat pairs this project may read     (out of git; add `!access.toml` to commit it)
.telepeep/tool/         the tool at the pinned version               (fetched by each person)
.telepeep/out/          downloaded attachments
.claude/skills/telepeep/SKILL.md                                     (committed)
```

```toml
# <config dir>/telepeep/config.toml
[telegram]
api_id = 12345
api_hash = "..."

[limits]
max_media_mb = 50

[sessions]
storage = "keyring"   # or "file": no OS secret store, sessions unencrypted

[accounts.work]
user_id = 111111111
label = "Work"
chats = [{ id = -1001234567890, name = "team" }]
```

```toml
# .telepeep/access.toml
default_account = "work"   # optional: which account wins when a bare name matches several
max_media_mb = 20          # optional: can only lower the machine-wide limit

[[grants]]
account = "work"
chat = -1001234567890  # work:team
```

A chat is readable when the project grants it **and** the account's catalog lists it **and** the account's session
exists and still belongs to the user recorded at login.

## Quick start

Requires git and [uv](https://docs.astral.sh/uv/) (uv fetches a suitable Python itself). From the project root:

```
git -c advice.detachedHead=false clone -q --depth 1 --branch vX.Y.Z https://github.com/ivzkv/telepeep.git .telepeep/tool
uv run --project .telepeep/tool --frozen --no-dev --quiet telepeep init
```

Then, in **your own terminal** (these commands refuse to run without one):

```
telepeep login work                  # once per account: api_id/api_hash (my.telegram.org) the first time, phone, code, 2FA
telepeep list work --filter team     # find the chat and its id
telepeep allow work -1001234567890   # add it to the catalog under a name you choose
telepeep grant work:team             # let this project read it
```

`telepeep` above is short for `uv run --project .telepeep/tool --frozen --no-dev --quiet telepeep`, run from the
project root. Commit `.telepeep/version`, `.telepeep/.gitignore` and the skill; others fetch the tool on first use
(the skill tells Claude how) and set up their own accounts and grants.

On Windows, run the user commands in PowerShell or Windows Terminal; Git Bash's mintty is not a console
(use `winpty` there).

## Commands

For the agent (`chats`, `history`, `search`, `media`, `status` print one JSON object on stdout, errors as
`{"error", "message", "hint"}`; `init`, `skill`, `version` print plain text; exit codes 2 usage, 3 no access,
4 not logged in / not configured, 5 Telegram or network):

```
telepeep chats                                         # this project's chats and their state
telepeep history <chat> [--limit N] [--since DATE | --after-id ID | --before-id ID]
telepeep search <chat> "<text>" [--limit N] [--since DATE] [--before-id ID]
telepeep media <chat> <msg_id>                         # → .telepeep/out/<account>/<chat_id>/<msg_id>.<ext>
telepeep status                                        # version, pin, skill, deny rules, accounts, chats
telepeep init | skill | version
```

`<chat>` is `account:name`, `account:id`, or a bare name / id unambiguous among the project's grants.
Messages come in ascending order; a full page has `more` with the `--before-id` / `--after-id` for the next one.

For the user only (interactive terminal):

```
telepeep login <account> [--label TEXT] | logout <account> | accounts
telepeep list <account> [--filter TEXT]
telepeep allow <account> <id|@username|t.me link> [--name NAME] | deny <account> <id|name>
telepeep grant <account:chat>… | revoke <account:chat>…
```

`deny` and `logout` keep the projects' grants: they stop working and come back if you allow the chat or log in again.

## Using with Claude Code

`skill` (also run by `init`) pins the project to the running version and installs its `SKILL.md`; `status` reports a
mismatch. To move to another version: write it to `.telepeep/version`, delete `.telepeep/tool/`, fetch again, run
`skill`, commit the diff.

Deny Claude access to the sessions and allowlists in the project's `.claude/settings.json` — `skill` and `status`
print the exact rules for your machine, e.g. on Windows:

```json
"permissions": { "deny": [
  "Read(~/AppData/Roaming/telepeep/**)", "Edit(~/AppData/Roaming/telepeep/**)",
  "Read(~/AppData/Local/telepeep/sessions/**)", "Edit(~/AppData/Local/telepeep/sessions/**)",
  "Edit(/.telepeep/access.toml)"
] }
```

## Security model, honestly

- The tool never writes to Telegram. That is enforced in code and tests.
- Access control is a guard against mistakes, not a sandbox against a hostile agent. The terminal check stops an agent
  from running `allow` / `grant` from its shell, and the deny rules keep Claude's file tools away from the config and
  sessions — but a process running as you can read your files, and a shell command is not a file tool. The catalog
  bounds what a misconfigured or edited project can reach.
- A session is full access to the Telegram account. Encryption makes a copied session file useless on its own — a
  backup, a synced folder, a stealer that collects session files — but not against a process running as you: it can
  ask the OS secret store for the key just as telepeep does. To limit what a leak exposes, consider a separate
  Telegram account for the agent, a member of only the chats it needs.
- If a session leaks, end it in Telegram → Settings → Devices (telepeep sessions show as "telepeep"), or
  `telepeep logout <account>`.

## Development

```
uv sync
uv run pytest
uv run telepeep --help
```

Release: bump `__version__` in `src/telepeep/__init__.py`, push to the `release` branch — the workflow tests, tags
`vX.Y.Z` and creates a GitHub Release.
