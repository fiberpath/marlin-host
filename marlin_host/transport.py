"""Transport abstraction for the Marlin host.

:class:`Transport` is the minimal line-oriented serial interface ``MarlinHost``
needs. :class:`FakeTransport` is an in-memory double that records what the host
wrote and replays scripted device responses, so the whole host stack is testable
without hardware. The pyserial-backed ``SerialTransport`` lands with the
connection lifecycle (it needs DTR/reset handling).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any, Protocol, runtime_checkable

__all__ = ["Transport", "FakeTransport", "SerialTransport", "TracingTransport"]

DEFAULT_BAUD_RATE = 250_000


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

    @classmethod
    def from_trace(cls, trace: Iterable[str]) -> FakeTransport:
        """Build a transport that replays a recorded session (see :class:`TracingTransport`).

        Lines are ``> <tx>`` (host→device) and ``< <rx>`` (device→host). Received
        lines before the first ``>`` are pre-fed (e.g. the ``start`` banner); the
        rx lines after each ``>`` are returned on the host's Nth write — so a
        captured session replays as a conformance fixture, hardware-free.
        """
        leading: list[str] = []
        groups: list[list[str]] = []
        current: list[str] | None = None
        for raw in trace:
            line = raw.rstrip("\r\n")
            if line.startswith("> "):
                current = []
                groups.append(current)
            elif line.startswith("< "):
                (current if current is not None else leading).append(line[2:])
        index = 0

        def responder(_tx: str) -> list[str]:
            nonlocal index
            replies = groups[index] if index < len(groups) else []
            index += 1
            return replies

        transport = cls(responder=responder)
        transport.feed(*leading)
        return transport

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


class SerialTransport:
    """pyserial-backed :class:`Transport`. Requires ``pip install marlin-host[serial]``.

    Opening the port and toggling DTR resets the controller (most boards reset on
    DTR), so it reboots and emits ``start`` — :meth:`MarlinHost.connect` then
    drains the banner. Pass ``reset_on_open=False`` to skip the toggle (e.g. for
    boards that do not reset on DTR, or to attach to a running controller).

    NOTE: the DTR-reset timing is board-specific and unvalidated against hardware;
    expect to tune :meth:`reset` during the first real session.
    """

    def __init__(
        self,
        port: str,
        baud_rate: int = DEFAULT_BAUD_RATE,
        *,
        timeout: float = 2.0,
        reset_on_open: bool = True,
    ) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "SerialTransport requires pyserial: pip install 'marlin-host[serial]'"
            ) from exc
        # Typed as Any so the optional pyserial dependency does not leak into the
        # type-checked surface.
        self._serial: Any = serial.serial_for_url(
            port, baudrate=baud_rate, timeout=timeout, write_timeout=timeout
        )
        if reset_on_open:
            self.reset()

    def reset(self) -> None:
        """Toggle DTR low→high to reset the controller (it reboots and emits ``start``)."""
        self._serial.dtr = False
        time.sleep(0.1)
        self._serial.reset_input_buffer()
        self._serial.dtr = True

    def write_line(self, line: str) -> None:
        self._serial.write((line + "\n").encode("ascii"))
        self._serial.flush()

    def read_line(self, timeout: float | None = None) -> str | None:
        if timeout is not None:
            self._serial.timeout = timeout
        raw = self._serial.readline()
        if not raw:
            return None
        decoded: str = raw.decode("ascii", errors="replace").strip()
        return decoded

    def close(self) -> None:
        self._serial.close()


class TracingTransport:
    """Decorate a :class:`Transport`, logging every line to ``sink``.

    Each transmitted line is logged as ``> <line>`` and each received line as
    ``< <line>``. Recording a real session this way captures a trace that can
    seed the conformance corpus.
    """

    def __init__(self, inner: Transport, sink: Callable[[str], None]) -> None:
        self._inner = inner
        self._sink = sink

    def write_line(self, line: str) -> None:
        self._sink(f"> {line}")
        self._inner.write_line(line)

    def read_line(self, timeout: float | None = None) -> str | None:
        line = self._inner.read_line(timeout)
        if line is not None:
            self._sink(f"< {line}")
        return line

    def close(self) -> None:
        self._inner.close()
