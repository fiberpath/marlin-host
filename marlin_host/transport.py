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

        A trace replayed through :meth:`MarlinHost.connect` should lead with the
        boot banner (``< start``) so connect detects readiness from the pre-fed
        lines; otherwise connect's ``M110`` probe consumes the first write group.
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

    Opening the port and pulsing DTR resets the controller (AVR/RAMPS and chip-fed
    32-bit boards reset on a DTR edge), so it reboots and re-emits ``start`` —
    :meth:`MarlinHost.connect` then waits for that greeting (or probes). Pass
    ``reset_on_open=False`` to skip the pulse (native-USB boards that ignore DTR,
    or to attach to a running controller).

    NOTE: the DTR-pulse timing is board-specific. The default ``assert_hold=0.2``
    is validated on RAMPS 1.4 / Marlin ``bugfix-2.1.x`` (reliably catches ``start``);
    other adapters may need tuning — see issue #4.
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

    def reset(
        self, *, dtr: bool = True, rts: bool = False, assert_hold: float = 0.2, flush: bool = False
    ) -> None:
        """Pulse the reset line so the controller reboots and re-emits ``start``.

        Follows the printrun sequence — assert DTR, hold ``assert_hold`` seconds,
        release — which puts a clean falling edge on the adapter's DTR pin. On
        AVR/RAMPS and chip-fed 32-bit boards that edge couples to ``RESET`` through
        a small capacitor; native-USB boards have no such cap and ignore it (fine —
        ``MarlinHost.connect`` falls back to probing). RTS is left at the driver
        default unless ``rts=True``; no standard Marlin board needs it.

        By default the input buffer is **not** flushed, so ``connect`` can match the
        ``start`` greeting on a normal open. Pass ``flush=True`` to discard buffered
        input right after the reset edge — required when *recovering* a board that
        already emitted stale output (e.g. ``Error:Printer halted. kill() called!``
        after an M112), which the next ``connect`` would otherwise consume and
        misreport as a fresh halt. The post-reboot ``start`` arrives after the boot
        delay, so it survives the flush. ``assert_hold`` is the board-specific
        timing knob; see issue #4.
        """
        if dtr:
            self._serial.dtr = True  # assert -> adapter DTR pin low -> RESET edge
        if rts:
            self._serial.rts = True
        time.sleep(assert_hold)
        if dtr:
            self._serial.dtr = False  # release
        if rts:
            self._serial.rts = False
        if flush:
            # Stale pre-reset output is already buffered; the reboot `start` is not
            # yet, so dropping the buffer now clears the former and keeps the latter.
            self._serial.reset_input_buffer()

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
