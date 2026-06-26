"""The synchronous Marlin host client.

:class:`MarlinHost` drives a controller over a :class:`~marlin_host.transport.Transport`:
connect, send a command and wait for its terminal response (``ok`` / error /
halt / resend), and stop out-of-band. The wait is **bounded** — a long move's
``echo:busy:`` keepalives are consumed, but an unresponsive controller (no line
within ``idle_timeout``) and an M0-style user pause both surface as errors
instead of hanging forever, and a fatal/halt line stops the host immediately.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import _constants as c
from .framing import frame
from .protocol import MarlinResponse, MarlinResponseKind, parse_response

if TYPE_CHECKING:
    from .transport import Transport

__all__ = ["MarlinHost", "HostError", "HaltError", "ProtocolError"]

DEFAULT_IDLE_TIMEOUT = 10.0
DEFAULT_STARTUP_TIMEOUT = 2.0
DEFAULT_MAX_RESENDS = 5

# Recoverable line-protocol errors: Marlin emits one of these, then a `Resend:`.
_LINE_ERRORS = (c.ERR_CHECKSUM_MISMATCH, c.ERR_NO_CHECKSUM, c.ERR_LINE_NO)


class HostError(RuntimeError):
    """Base error for host/controller communication failures."""


class HaltError(HostError):
    """The controller halted/stopped (kill, thermal, M112); a reset is required."""


class ProtocolError(HostError):
    """The controller reported an error, or the exchange could not be completed."""


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

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_halted(self) -> bool:
        return self._halted

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
        if self._reliable:
            self._line_number += 1
            self._t.write_line(frame(self._line_number, command))
        else:
            self._t.write_line(command)
        return self._await_terminal(command)

    def emergency_stop(self) -> None:
        """Send M112 out-of-band (no framing, no waiting) and mark the host halted."""
        self._t.write_line("M112")
        self._halted = True

    def close(self) -> None:
        self._t.close()
        self._connected = False

    def _await_terminal(self, command: str) -> MarlinResponse:
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

            # echo / reports / start / wait / unknown — not terminal, keep reading.
