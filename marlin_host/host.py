"""The synchronous Marlin host client.

:class:`MarlinHost` drives a controller over a :class:`~marlin_host.transport.Transport`:
connect, send a command and wait for its terminal response (``ok`` / error /
halt / resend), stream a program with pause/resume/stop, query capabilities, and
stop out-of-band. The wait is **bounded** — a long move's ``echo:busy:``
keepalives are consumed, but an unresponsive controller (no line within
``idle_timeout``) and an M0-style user pause both surface as errors instead of
hanging forever, and a fatal/halt line stops the host immediately.

Streaming is plain send-one-await-``ok`` pacing (no look-ahead buffer), so
pause/resume/stop are host-side: pausing simply stops sending the next line.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import _constants as c
from .framing import frame
from .protocol import MarlinResponse, MarlinResponseKind, parse_response

if TYPE_CHECKING:
    from .transport import Transport

__all__ = [
    "MarlinHost",
    "HostError",
    "HaltError",
    "ProtocolError",
    "StreamProgress",
    "Capabilities",
]

DEFAULT_IDLE_TIMEOUT = 10.0
DEFAULT_STARTUP_TIMEOUT = 2.0
DEFAULT_MAX_RESENDS = 5
DEFAULT_PAUSE_POLL = 0.05

# Recoverable line-protocol errors: Marlin emits one of these, then a `Resend:`.
_LINE_ERRORS = (c.ERR_CHECKSUM_MISMATCH, c.ERR_NO_CHECKSUM, c.ERR_LINE_NO)


class HostError(RuntimeError):
    """Base error for host/controller communication failures."""


class HaltError(HostError):
    """The controller halted/stopped (kill, thermal, M112); a reset is required."""


class ProtocolError(HostError):
    """The controller reported an error, or the exchange could not be completed."""


@dataclass(frozen=True)
class StreamProgress:
    """Progress after one streamed command completes."""

    commands_sent: int
    total_commands: int
    command: str
    response: MarlinResponse


@dataclass(frozen=True)
class Capabilities:
    """Parsed M115 report: firmware line + capability flags."""

    firmware: str
    caps: Mapping[str, bool]

    def has(self, name: str) -> bool:
        """True if the controller reported capability ``name`` enabled."""
        return self.caps.get(name, False)


def _is_line_error(message: str | None) -> bool:
    return message is not None and message.startswith(_LINE_ERRORS)


class MarlinHost:
    """A synchronous host for a Marlin controller over a line transport."""

    def __init__(
        self,
        transport: Transport,
        *,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        reliable: bool = False,
        max_resends: int = DEFAULT_MAX_RESENDS,
    ) -> None:
        self._t = transport
        self._idle_timeout = idle_timeout
        self._startup_timeout = startup_timeout
        self._reliable = reliable
        self._max_resends = max_resends
        self._line_number = 0
        self._halted = False
        self._connected = False
        self._paused = False
        self._stopped = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def line_number(self) -> int:
        return self._line_number

    def connect(self) -> None:
        """Drain the startup banner until the controller goes idle (ready).

        Consumes any ``start`` banner and config echoes; returns once a read
        times out (no more startup chatter). An already-running controller that
        emits nothing simply returns immediately.
        """
        self._halted = False
        self._line_number = 0
        while self._t.read_line(self._startup_timeout) is not None:
            pass
        self._connected = True

    def send(self, command: str) -> MarlinResponse:
        """Send one command and return its terminal ``ok`` response.

        Raises :class:`HaltError` on a fatal/halt line, :class:`ProtocolError`
        on a reported error or exhausted resends, and :class:`HostError` if the
        controller is unresponsive or paused for user/input.
        """
        if self._halted:
            raise HaltError("controller is halted; a reset is required")
        self._write(command)
        return self._await_terminal(command)

    def query(self, command: str) -> list[MarlinResponse]:
        """Send a reporting command and return its intermediate response lines.

        Like :meth:`send`, but collects the data lines that precede ``ok`` (e.g.
        the ``FIRMWARE_NAME:``/``Cap:`` block of M115, or an M114 position line).
        """
        if self._halted:
            raise HaltError("controller is halted; a reset is required")
        collected: list[MarlinResponse] = []
        self._write(command)
        self._await_terminal(command, collected)
        return collected

    def capabilities(self) -> Capabilities:
        """Query M115 and return the parsed firmware line + capability flags."""
        firmware = ""
        caps: dict[str, bool] = {}
        for resp in self.query("M115"):
            if resp.kind is MarlinResponseKind.FIRMWARE:
                firmware = resp.message or resp.raw
            elif resp.kind is MarlinResponseKind.CAPABILITY and resp.capability is not None:
                name, enabled = resp.capability
                caps[name] = enabled
        return Capabilities(firmware=firmware, caps=caps)

    def stream(
        self, program: Iterable[str], *, poll_interval: float = DEFAULT_PAUSE_POLL
    ) -> Iterator[StreamProgress]:
        """Stream a G-code program line by line, yielding progress per command.

        Blank lines and ``;`` comments are skipped. While :meth:`pause` is in
        effect the stream blocks before the next line; :meth:`stop` ends it
        early; :meth:`resume` continues it.
        """
        commands = [stripped for line in program if (stripped := line.strip())]
        commands = [line for line in commands if not line.startswith(";")]
        self._stopped = False
        for index, command in enumerate(commands, start=1):
            while self._paused and not self._stopped:
                time.sleep(poll_interval)
            if self._stopped:
                return
            response = self.send(command)
            yield StreamProgress(index, len(commands), command, response)

    def pause(self) -> None:
        """Pause streaming before the next line (host-side)."""
        self._paused = True

    def resume(self) -> None:
        """Resume a paused stream."""
        self._paused = False

    def stop(self) -> None:
        """End the active stream before its next line."""
        self._stopped = True

    def emergency_stop(self) -> None:
        """Send M112 out-of-band (no framing, no waiting) and mark the host halted."""
        self._t.write_line("M112")
        self._halted = True

    def close(self) -> None:
        self._t.close()
        self._connected = False

    def _write(self, command: str) -> None:
        if self._reliable:
            self._line_number += 1
            self._t.write_line(frame(self._line_number, command))
        else:
            self._t.write_line(command)

    def _await_terminal(
        self, command: str, collect: list[MarlinResponse] | None = None
    ) -> MarlinResponse:
        resends = 0
        while True:
            line = self._t.read_line(self._idle_timeout)
            if line is None:
                raise HostError(
                    f"no response within {self._idle_timeout}s — controller unresponsive"
                )
            resp = parse_response(line)

            if resp.is_ack:
                return resp

            if resp.is_fatal:
                self._halted = True
                raise HaltError(resp.message or resp.raw)

            if resp.needs_resend:
                if not self._reliable:
                    raise ProtocolError(f"unexpected resend (reliable mode off): {resp.raw}")
                resends += 1
                if resends > self._max_resends:
                    raise ProtocolError(f"exceeded {self._max_resends} resend retries")
                self._t.write_line(frame(self._line_number, command))
                continue

            if resp.kind is MarlinResponseKind.BUSY:
                if resp.message and resp.message.startswith("paused"):
                    raise HostError(f"controller is busy: {resp.message}; use the pause/resume API")
                continue  # busy: processing — keepalive, keep waiting

            if resp.kind is MarlinResponseKind.ERROR:
                if self._reliable and _is_line_error(resp.message):
                    continue  # a `Resend:` follows this recoverable line error
                raise ProtocolError(resp.message or resp.raw)

            # echo / reports / start / wait / unknown — not terminal; collect if asked.
            if collect is not None:
                collect.append(resp)
