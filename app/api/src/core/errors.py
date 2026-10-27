from collections.abc import Iterable


class NotFound(LookupError):
    """The run, project or organization doesn't exist, or was deleted, renamed or moved (404)."""


class Discarded(Exception):
    """A write from the client of a run deleted while it was running. It is acknowledged and
    dropped, so the client finishes normally and the run stays deleted."""

    def __init__(self, project: str, name: str) -> None:
        super().__init__(f"run {project}/{name} was deleted")
        self.project = project
        self.name = name


class Conflict(Exception):
    """A mutation that can't be applied as asked (HTTP 409); ``ids`` names the runs in the way."""

    def __init__(self, message: str, ids: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.ids = list(ids)


class Forbidden(Exception):
    """The user may not do this (HTTP 403); ``ids`` names the runs they may not change."""

    def __init__(self, message: str, ids: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.ids = list(ids)


class TooManyAttempts(Exception):
    """Too many wrong guesses, such as sign-in codes, in a short time (HTTP 429)."""
