"""Where an attachment goes and under which name.

The name is never taken from Telegram: `<msg_id><ext>`, where the extension comes from a fixed list (anything else →
`.bin`). No path separators, reserved Windows names (CON, NUL…), trailing dots or overly long names can reach the disk.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from .config import Config
from .project import OUT_DIR, Access

ALLOWED_EXT = {
    ".jpg", ".png", ".gif", ".webp", ".heic", ".bmp", ".svg",
    ".mp4", ".mov", ".webm", ".mkv", ".avi",
    ".mp3", ".ogg", ".oga", ".opus", ".m4a", ".wav", ".flac",
    ".pdf", ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".log",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".rtf",
    ".zip", ".7z", ".rar", ".tar", ".gz",
    ".tgs",
}
ALIASES = {".jpeg": ".jpg", ".jpe": ".jpg", ".htm": ".html", ".text": ".txt"}


def safe_ext(ext: str | None, mime: str | None) -> str:
    candidate = (ext or (mimetypes.guess_extension(mime) if mime else None) or "").lower()
    candidate = ALIASES.get(candidate, candidate)
    return candidate if candidate in ALLOWED_EXT else ".bin"


def target_path(account: str, chat_id: int, msg_id: int, ext: str) -> Path:
    return OUT_DIR / account / str(chat_id) / f"{msg_id}{ext}"


def limit_mb(cfg: Config, access: Access) -> int:
    """The project may only lower the machine-wide limit."""
    if access.max_media_mb is None:
        return cfg.max_media_mb
    return min(cfg.max_media_mb, access.max_media_mb)
