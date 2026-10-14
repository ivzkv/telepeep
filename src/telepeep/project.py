"""The project's own state, in .telepeep/ of the current folder (parent folders are not searched).

  .telepeep/version       pinned tool version                                   (committed)
  .telepeep/.gitignore    keeps everything else out of git                      (committed)
  .telepeep/access.toml   account/chat pairs this project may read              (out of git unless the user opts in)
  .telepeep/tool/         the source of the pinned tag, run with uv             (fetched by each person)
  .telepeep/out/          downloaded attachments
  .claude/skills/telepeep/SKILL.md   the skill of the pinned version            (committed)

Paths are relative: they resolve against the folder the tool is run from, which must be the project root.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import RUN, __version__, config
from .config import NAME_RE, write_atomic
from .errors import EXIT_NO_ACCESS, TelepeepError

VERSION = __version__
REPO = "ivzkv/telepeep"

PROJECT_DIR = Path(".telepeep")
VERSION_FILE = PROJECT_DIR / "version"
ACCESS_FILE = PROJECT_DIR / "access.toml"
GITIGNORE_FILE = PROJECT_DIR / ".gitignore"
TOOL_DIR = PROJECT_DIR / "tool"
OUT_DIR = PROJECT_DIR / "out"
SKILL_DST = Path(".claude") / "skills" / "telepeep" / "SKILL.md"

# Written only when missing, so a user who chose to commit access.toml keeps their edit
GITIGNORE = (
    "# telepeep: only the version pin is shared by default.\n"
    "# To commit this project's grants (account aliases and chat ids) too, add the line: !access.toml\n"
    "*\n!.gitignore\n!version\n"
)


def fetch_commands(version: str) -> str:
    """Puts the source of tag v<version> into .telepeep/tool (the folder must not exist); uv builds its venv on first run."""
    return (f"git -c advice.detachedHead=false clone -q --depth 1 --branch v{version} "
            f"https://github.com/{REPO}.git {TOOL_DIR.as_posix()}")


# ---------- access.toml ----------

@dataclass
class Grant:
    account: str
    chat: int


@dataclass
class Access:
    default_account: str | None = None  # picks the account when a bare chat name or id matches grants of several
    max_media_mb: int | None = None  # can only lower the machine-wide limit
    grants: list[Grant] = field(default_factory=list)

    def has(self, account: str, chat: int) -> bool:
        return any(g.account == account and g.chat == chat for g in self.grants)


def _bad(message: str) -> TelepeepError:
    return TelepeepError("bad_project", f"{ACCESS_FILE.resolve()}: {message}", exit_code=EXIT_NO_ACCESS)


def load_access() -> Access:
    if not ACCESS_FILE.exists():
        raise TelepeepError(
            "no_project", f"{ACCESS_FILE.resolve()} does not exist — this folder is not a telepeep project",
            hint=f"run from the project root; to set the project up: {RUN} init, then the user grants chats with "
                 f"`{RUN} grant <account>:<chat>`",
            exit_code=EXIT_NO_ACCESS)
    try:
        raw = tomllib.loads(ACCESS_FILE.read_text("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise _bad(str(e)) from None

    acc = Access(default_account=raw.get("default_account"), max_media_mb=raw.get("max_media_mb"))
    if acc.default_account is not None and not (isinstance(acc.default_account, str) and NAME_RE.match(acc.default_account)):
        raise _bad("default_account must be an account alias")
    if acc.max_media_mb is not None and (type(acc.max_media_mb) is not int or acc.max_media_mb < 0):
        raise _bad("max_media_mb must be a non-negative integer")
    for i, g in enumerate(raw.get("grants", [])):
        account, chat = g.get("account"), g.get("chat")
        if not (isinstance(account, str) and NAME_RE.match(account)) or type(chat) is not int:
            raise _bad(f"grants[{i}] needs account = \"<alias>\" and chat = <numeric id>")
        if not acc.has(account, chat):
            acc.grants.append(Grant(account, chat))
    return acc


def save_access(access: Access, cfg: config.Config) -> None:
    # Own writer instead of tomli_w: keeps a header and puts the catalog name next to each id for whoever reads the file
    lines = ["# telepeep: Telegram chats this project may read. Change with `telepeep grant` / `telepeep revoke`.",
             "# A grant works only while the chat is in its account's catalog on this machine (`telepeep allow`)."]
    if access.default_account or access.max_media_mb is not None:
        lines.append("")
    if access.default_account:
        lines.append(f'default_account = "{access.default_account}"')
    if access.max_media_mb is not None:
        lines.append(f"max_media_mb = {access.max_media_mb}")
    for g in access.grants:
        acc = cfg.accounts.get(g.account)
        cat = acc.chat_by_id(g.chat) if acc else None
        lines += ["", "[[grants]]", f'account = "{g.account}"',
                  f"chat = {g.chat}" + (f"  # {g.account}:{cat.name}" if cat else "")]
    write_atomic(ACCESS_FILE, "\n".join(lines) + "\n")


# ---------- version pin and skill ----------

def _read(path: Path) -> str | None:
    # Compare ignoring CRLF/LF: git on Windows may convert line endings
    return path.read_text("utf-8").replace("\r\n", "\n") if path.exists() else None


def pinned_version() -> str | None:
    return (_read(VERSION_FILE) or "").strip() or None


def skill_text() -> str:
    src = resources.files("telepeep").joinpath("skill/SKILL.md").read_text("utf-8").replace("\r\n", "\n")
    return src.rstrip() + "\n\n<!-- Generated by telepeep (`skill` command). Do not edit: the file is overwritten on update. -->\n"


def mismatch_hint(pinned: str) -> str:
    return (f"to run v{pinned}: delete {TOOL_DIR.as_posix()}, then {fetch_commands(pinned)}; "
            f"to pin v{VERSION} instead: {RUN} skill")


def setup_state() -> dict:
    """For status: the project's pin and skill compared with the running version."""
    pinned = pinned_version()
    skill = _read(SKILL_DST)
    return {
        "pinned": pinned,
        "running": VERSION,
        "pin": ("missing" if not pinned else "matches" if pinned == VERSION else "differs"),
        "skill": ("missing" if skill is None else "matches" if skill == skill_text() else "differs"),
        "access_file": ACCESS_FILE.exists(),
    }


def init() -> list[str]:
    PROJECT_DIR.mkdir(exist_ok=True)
    out = []
    if ACCESS_FILE.exists():
        out.append(f"- {ACCESS_FILE.resolve()} already exists — left untouched")
    else:
        save_access(Access(), config.Config())
        out.append(f"- Created {ACCESS_FILE.resolve()} — no chats granted yet")
    return out + install_skill()


def install_skill() -> list[str]:
    """Pins the project to the running version, installs its skill and the .telepeep/.gitignore. Writes only here."""
    PROJECT_DIR.mkdir(exist_ok=True)
    out = []
    if not GITIGNORE_FILE.exists():
        GITIGNORE_FILE.write_text(GITIGNORE, "utf-8", newline="\n")
        out.append(f"- {GITIGNORE_FILE.resolve()}: .telepeep/ stays out of git except the version pin")
    pinned = pinned_version()
    if pinned != VERSION:
        VERSION_FILE.write_text(VERSION + "\n", "utf-8", newline="\n")
    out.append(f"- Version {VERSION_FILE.resolve()}: {f'v{pinned} → ' if pinned and pinned != VERSION else ''}v{VERSION}")
    current = _read(SKILL_DST)
    if current == skill_text():
        out.append(f"- Skill {SKILL_DST.resolve()} is up to date")
    else:
        SKILL_DST.parent.mkdir(parents=True, exist_ok=True)
        SKILL_DST.write_text(skill_text(), "utf-8", newline="\n")
        out.append(f"- Skill {'installed' if current is None else 'updated'}: {SKILL_DST.resolve()}")
    missing = missing_deny_rules()
    if missing:
        out.append('- ⚠️ .claude/settings.json does not deny Claude access to the sessions and the allowlists; add to '
                   '"permissions": { "deny": ' + json.dumps(missing, ensure_ascii=False) + " }")
    return out


# ---------- Claude Code deny rules ----------

def _rule_path(path: Path) -> str:
    """Claude Code permission path: `~/…` under the home folder, `//…` for other absolute paths."""
    path = path.resolve()
    try:
        return "~/" + path.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return "//" + path.as_posix().lstrip("/")


def deny_rules() -> list[str]:
    rules = []
    for d in (config.CONFIG_DIR, config.SESSIONS_DIR):
        p = _rule_path(d)
        rules += [f"Read({p}/**)", f"Edit({p}/**)"]
    rules.append(f"Edit(/{ACCESS_FILE.as_posix()})")
    return rules


def missing_deny_rules() -> list[str]:
    """Rules from deny_rules() absent from the project's .claude/settings*.json. The files are never changed here."""
    present: set[str] = set()
    for name in ("settings.json", "settings.local.json"):
        text = _read(Path(".claude") / name)
        if text:
            try:
                present.update(json.loads(text).get("permissions", {}).get("deny", []))
            except (json.JSONDecodeError, AttributeError):
                pass
    return [r for r in deny_rules() if r not in present]

