"""Transport abstraction for the Marlin host.

:class:`Transport` is the minimal line-oriented serial interface ``MarlinHost``
needs. :class:`FakeTransport` is an in-memory double that records what the host
wrote and replays scripted device responses, so the whole host stack is testable
without hardware. The pyserial-backed ``SerialTransport`` lands with the
connection lifecycle (it needs DTR/reset handling).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from typing import Protocol, runtime_checkable

__all__ = ["Transport", "FakeTransport"]


@runtime_checkable
class Transport(Protocol):
    """A line-oriented serial transport."""

    def write_line(self, line: str) -> None:
        """Send one line to the device (the newline terminator is the transport's job)."""

    def read_line(self, timeout: float | None = None) -> str | None:
        """Return one received line, or ``None`` if none arrived within ``timeout``."""

    def close(self) -> None:
        """Release the underlying resource."""


class FakeTransport:
    """In-memory :class:`Transport` for tests.

    Pre-queue device→host lines with :meth:`feed`, and/or pass a ``responder``
    that returns the device's reply lines for each written line (queued for
    subsequent reads). :attr:`written` records everything the host sent.
    """

    def __init__(self, responder: Callable[[str], Iterable[str]] | None = None) -> None:
        self._responder = responder
        self._incoming: deque[str] = deque()
        self.written: list[str] = []
        self.closed = False

    def feed(self, *lines: str) -> None:
        """Queue device→host lines to be returned by :meth:`read_line`."""
        self._incoming.extend(lines)

    def write_line(self, line: str) -> None:
        self.written.append(line)
        if self._responder is not None:
            self._incoming.extend(self._responder(line))

    def read_line(self, timeout: float | None = None) -> str | None:
        if self._incoming:
            return self._incoming.popleft()
        return None

    def close(self) -> None:
        self.closed = True
