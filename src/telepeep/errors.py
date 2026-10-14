"""Errors the CLI reports: a stable code for the agent, a message, and the next step for the user."""

EXIT_USAGE = 2
EXIT_NO_ACCESS = 3  # not granted, not in the catalog, no project, or a user-only command run without a terminal
EXIT_NOT_READY = 4  # no config, no session, session of another user
EXIT_TELEGRAM = 5  # Telegram or network error


class TelepeepError(Exception):
    def __init__(self, code: str, message: str, *, hint: str | None = None, exit_code: int = EXIT_USAGE, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.exit_code = exit_code
        self.extra = extra

    def as_json(self) -> dict:
        out = {"error": self.code, "message": self.message, **self.extra}
        if self.hint:
            out["hint"] = self.hint
        return out


def no_access(message: str, hint: str | None = None) -> TelepeepError:
    return TelepeepError("no_access", message, hint=hint, exit_code=EXIT_NO_ACCESS)
