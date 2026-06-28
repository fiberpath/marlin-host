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
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import _constants as c
from .framing import frame, reset_line_number
from .protocol import MarlinResponse, MarlinResponseKind, parse_response

if TYPE_CHECKING:
    from .transport import Transport

__all__ = [
    "MarlinHost",
    "HostError",
    "HaltError",
    "ProtocolError",
    "StreamProgress",
    "Profile",
    "Capabilities",
]

DEFAULT_IDLE_TIMEOUT = 10.0
DEFAULT_STARTUP_TIMEOUT = 2.0
DEFAULT_MAX_RESENDS = 5
DEFAULT_PAUSE_POLL = 0.05
DEFAULT_CONNECT_PROBES = 3

# Recoverable line-protocol errors: Marlin emits one of these, then a `Resend:`.
_LINE_ERRORS = (c.ERR_CHECKSUM_MISMATCH, c.ERR_NO_CHECKSUM, c.ERR_LINE_NO)


def _is_boot_banner(line: str) -> bool:
    """True for Marlin's unconditional boot lines (``start`` / ``Marlin <ver>``)."""
    stripped = line.strip()
    return stripped == "start" or stripped.startswith("Marlin ")


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
class Profile:
    """A resolved Marlin dialect: one concrete build's host-facing behavior.

    The *negotiated* half — ``firmware`` and ``caps`` — is what
    :meth:`MarlinHost.capabilities` parses from the M115 report.
    :meth:`MarlinHost.connect` additionally infers ``advanced_ok`` from the first
    ``ok`` and applies any caller overrides, exposing the result at
    :attr:`MarlinHost.profile`. A cap absent from the report is treated as off
    (Marlin only advertises a cap when its build enables it).
    """

    firmware: str
    caps: Mapping[str, bool]
    # Dialect variants that Marlin does NOT report as caps:
    advanced_ok: bool = False  # `ok N.. P.. B..` (queue.cpp ADVANCED_OK); inferred at connect
    emits_wait_when_idle: bool = False  # `wait` heartbeat when idle (NO_TIMEOUTS); declared

    def has(self, name: str) -> bool:
        """True if the controller reported capability ``name`` enabled."""
        return self.caps.get(name, False)

    @property
    def emergency_stop_immediate(self) -> bool:
        """True iff the build has ``EMERGENCY_PARSER`` — without it, an M112 is
        parsed in order and only acts once buffered motion drains, so a caller
        cannot treat :meth:`MarlinHost.emergency_stop` as truly instantaneous."""
        return self.has("EMERGENCY_PARSER")


# Backwards-compatible alias: the negotiated M115 view is just a Profile.
Capabilities = Profile


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
        connect_probes: int = DEFAULT_CONNECT_PROBES,
        on_action: Callable[[MarlinResponse], None] | None = None,
    ) -> None:
        self._t = transport
        self._idle_timeout = idle_timeout
        self._startup_timeout = startup_timeout
        self._reliable = reliable
        self._max_resends = max_resends
        self._connect_probes = connect_probes
        self._on_action = on_action
        self._line_number = 0
        self._halted = False
        self._connected = False
        self._paused = False
        self._stopped = False
        self._profile: Profile | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def profile(self) -> Profile | None:
        """The dialect profile resolved at :meth:`connect` (``None`` before
        connecting, or when connected with ``negotiate=False``)."""
        return self._profile

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def line_number(self) -> int:
        return self._line_number

    def connect(
        self,
        *,
        negotiate: bool = True,
        advanced_ok: bool | None = None,
        emits_wait_when_idle: bool = False,
    ) -> None:
        """Establish that the controller is up and ready, and resolve its profile.

        Readiness: prefer Marlin's unconditional ``start`` boot line (emitted after
        a reset), draining the rest of the banner until quiet. For an already-running
        board — one that ignored a DTR reset, or whose ``start`` was missed — actively
        probe with a framed ``M110 N0`` and accept the resulting ``ok``, which also
        syncs line numbering. Raise :class:`HostError` if the controller never responds
        (wrong port/baud, no power) instead of falsely reporting ready.

        Profile: unless ``negotiate=False``, query M115 and expose the resolved
        :class:`Profile` at :attr:`profile` — firmware + capability flags, with
        ``advanced_ok`` inferred from the first ``ok`` (override it, and
        ``emits_wait_when_idle``, via the keyword args). Negotiation is tolerant: a
        board that does not answer M115 yields an empty profile rather than failing
        the connection (readiness is already proven by this point).
        """
        self._connected = False
        self._halted = False
        self._line_number = 0
        self._establish_ready()
        profile = self._negotiate(advanced_ok, emits_wait_when_idle) if negotiate else None
        self._connected = True
        self._profile = profile

    def _establish_ready(self) -> None:
        """Block until the controller proves it is ready, or raise."""
        # Phase 1: read the boot stream. An `ok`/report line proves readiness
        # outright; the `start`/`Marlin` banner then quiet == ready; a `wait`/`busy`
        # keepalive is a sign of life — ready if we already saw the banner, else
        # break to the probe (an idle board streams `wait` forever, so we must not
        # wait for silence here).
        saw_banner = False
        while (line := self._t.read_line(self._startup_timeout)) is not None:
            resp = self._parse_connect(line)
            if self._is_ready(resp):
                return
            if resp.is_keepalive:
                if saw_banner:
                    return
                break
            saw_banner = saw_banner or _is_boot_banner(line)
        if saw_banner:
            return

        # Phase 2: nothing decisive arrived — probe a running / reset-ignoring board.
        for _ in range(self._connect_probes):
            self._t.write_line(reset_line_number(0))  # framed M110 N0 forces an `ok`
            while (line := self._t.read_line(self._startup_timeout)) is not None:
                if self._is_ready(self._parse_connect(line)):
                    return
        raise HostError(
            "controller did not respond on connect — check the port, baud rate, and power"
        )

    def _negotiate(self, advanced_ok: bool | None, emits_wait_when_idle: bool) -> Profile:
        """Assemble the dialect profile from M115 plus caller overrides.

        minimal: the "declared baseline ∩ negotiated" intersection degenerates to
        "negotiated caps, absent ⇒ off" — enumerating every cap the pinned firmware
        *could* advertise has no consumer (the safe-degradation rule treats an absent
        cap as off regardless). Add a per-version baseline only if something needs to
        distinguish "cap not in this firmware" from "cap disabled in this build".
        """
        firmware, caps, terminal = self._query_m115()
        if advanced_ok is None:
            # ADVANCED_OK appends `P<planner> B<block>` to every `ok`; `P` is unique
            # to that form (M105/M114 reports never carry a bare `P`).
            advanced_ok = (
                terminal is not None and terminal.fields is not None and "P" in terminal.fields
            )
        return Profile(
            firmware=firmware,
            caps=caps,
            advanced_ok=advanced_ok,
            emits_wait_when_idle=emits_wait_when_idle,
        )

    def _query_m115(self) -> tuple[str, dict[str, bool], MarlinResponse | None]:
        """Send M115 (bare — a query must not consume a reliable line number) and
        gather firmware + caps + the terminal ``ok`` (``None`` if the board is silent)."""
        firmware = ""
        caps: dict[str, bool] = {}
        terminal: MarlinResponse | None = None
        self._t.write_line("M115")
        while (line := self._t.read_line(self._startup_timeout)) is not None:
            resp = parse_response(line)
            if resp.is_fatal:
                self._halted = True
                raise HaltError(resp.message or resp.raw)
            if resp.kind is MarlinResponseKind.FIRMWARE:
                firmware = resp.message or resp.raw
            elif resp.kind is MarlinResponseKind.CAPABILITY and resp.capability is not None:
                caps[resp.capability[0]] = resp.capability[1]
            elif resp.is_ack:
                terminal = resp
                break
        return firmware, caps, terminal

    def _parse_connect(self, line: str) -> MarlinResponse:
        """Parse a line during connect; raise :class:`HaltError` on a fatal line."""
        resp = parse_response(line)
        if resp.is_fatal:
            self._halted = True
            raise HaltError(resp.message or resp.raw)
        return resp

    @staticmethod
    def _is_ready(resp: MarlinResponse) -> bool:
        """True if ``resp`` proves the controller is ready to accept commands."""
        return resp.is_ack or resp.kind in (
            MarlinResponseKind.TEMPERATURE,
            MarlinResponseKind.POSITION,
        )

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

    def capabilities(self) -> Profile:
        """Query M115 and return the negotiated firmware line + capability flags.

        This is the negotiated half of the :class:`Profile` seam; :meth:`connect`
        wraps the same exchange to also infer ``advanced_ok``. Tolerant of a silent
        board (returns an empty profile rather than raising).
        """
        if self._halted:
            raise HaltError("controller is halted; a reset is required")
        firmware, caps, _ = self._query_m115()
        return Profile(firmware=firmware, caps=caps)

    def temperatures(self) -> Mapping[str, float]:
        """Query M105 and return the reported temperature fields, e.g.
        ``{'T': 20.6, 'B': 0.0, '@': 0.0}`` (hotend ``T``, bed ``B``, power ``@``).

        Unlike M114, Marlin carries the M105 report on the ``ok`` line itself, so —
        unlike :meth:`query` — the data is the terminal response's fields, not a
        preceding report line. Returns an empty mapping if the board reports none.
        """
        return self.send("M105").fields or {}

    def position(self) -> Mapping[str, float]:
        """Query M114 and return the reported axis position, e.g.
        ``{'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'E': 0.0}``.

        Symmetric with :meth:`temperatures`. Unlike M105, M114's report is a
        line *before* ``ok``, so the data comes from the collected report (the
        machine-step ``Count`` tail is dropped by the parser). Returns an empty
        mapping if the board reports no position line.
        """
        for resp in self.query("M114"):
            if resp.kind is MarlinResponseKind.POSITION and resp.fields is not None:
                return resp.fields
        return {}

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
        """Send M112 out-of-band (no framing, no waiting) and mark the host halted.

        Truly immediate only on a build with ``EMERGENCY_PARSER`` — check
        :attr:`Profile.emergency_stop_immediate` (via :attr:`profile`). Without it,
        M112 is parsed in order and acts only once buffered motion drains.
        """
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
        awaiting_resend_ack = False
        while True:
            line = self._t.read_line(self._idle_timeout)
            if line is None:
                raise HostError(
                    f"no response within {self._idle_timeout}s — controller unresponsive"
                )
            resp = parse_response(line)

            if resp.is_ack:
                if awaiting_resend_ack:
                    # Marlin emits `ok` right after `Resend:` (queue.cpp:276) to ack the
                    # resend *request*, before it processes the resent line. Swallow that
                    # one and keep waiting for the resent line's real `ok`; returning here
                    # would desync reliable streaming by one ack on every recovery.
                    awaiting_resend_ack = False
                    continue
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
                awaiting_resend_ack = True
                continue

            if resp.kind is MarlinResponseKind.BUSY:
                if resp.message and resp.message.startswith("paused"):
                    raise HostError(f"controller is busy: {resp.message}; use the pause/resume API")
                continue  # busy: processing — keepalive, keep waiting

            if resp.kind is MarlinResponseKind.ERROR:
                if self._reliable and _is_line_error(resp.message):
                    continue  # a `Resend:` follows this recoverable line error
                raise ProtocolError(resp.message or resp.raw)

            # Board-initiated host action (//action:pause/resume/cancel/prompt, e.g.
            # M600/runout/LCD). Non-terminal; deliver to the consumer (it would
            # otherwise be silently dropped) before falling through to collect.
            if resp.kind is MarlinResponseKind.ACTION and self._on_action is not None:
                self._on_action(resp)

            # echo / reports / start / wait / unknown / action — not terminal; collect if asked.
            if collect is not None:
                collect.append(resp)
