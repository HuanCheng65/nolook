"""What Device needs from the thing it drives.

Device keeps everything that does not depend on the platform: refs, queries,
settling, the write-action and covered checks, scrolling until something
shows. A backend reads the screen and performs the raw input.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from PIL.Image import Image

    from .tree import Node


class NolookError(Exception):
    pass


class Backend(Protocol):
    id: str  # names the cache directory and the records
    size: tuple[int, int]

    def dump(self) -> list[Node]:
        """Every visible element, in draw order."""

    def click(self, x: int, y: int) -> None: ...

    def long_click(self, x: int, y: int) -> None: ...

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float) -> None: ...

    def press(self, name: str) -> None: ...

    def set_text(self, value: str) -> None:
        """Replace the text of the focused field."""

    def screenshot(self) -> Image: ...

    def open_uri(self, uri: str, package: str | None) -> None: ...

    def launcher(self, package: str) -> str:
        """What `start` opens for this app; raises when it has none."""

    def stop(self, package: str) -> None: ...

    def start(self, launcher: str) -> None: ...

    def log(self, lines: int, grep: str | None, level: str | None, package: str | None) -> list[str]: ...

    def log_mark(self) -> str:
        """A point in the log that `log_since` reads from."""

    def log_since(self, mark: str) -> str: ...

    def interruption(self, package: str | None) -> str | None:
        """Why the app may not be showing what a step expects, if something is in the way."""
