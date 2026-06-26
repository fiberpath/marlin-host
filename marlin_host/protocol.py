"""Formal taxonomy and parser for Marlin host-facing serial responses.

A single pure function, :func:`parse_response`, classifies one received serial
line into a structured :class:`MarlinResponse`. This is the foundational
contract of the library: instead of ad-hoc string matching scattered across a
host implementation, every device->host line is mapped to one explicit taxonomy.

The exact literals come from :mod:`marlin_host._constants`, which is generated
from the pinned Marlin firmware source (``just codegen``) — the binding
host-protocol contract: ``ok``/ADVANCED_OK, ``Resend:``, ``Error:``,
halt/``kill()``, ``echo:busy:`` keepalive, ``//action:``, M115 ``Cap:``,
M114/M105 reports, ``start``/``wait``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from . import _constants as c

__all__ = ["MarlinResponseKind", "MarlinResponse", "parse_response"]


class MarlinResponseKind(Enum):
    OK = "ok"
    RESEND = "resend"
    ERROR = "error"
    HALT = "halt"
    BUSY = "busy"
    ACTION = "action"
    ECHO = "echo"
    FIRMWARE = "firmware"
    CAPABILITY = "capability"
    TEMPERATURE = "temperature"
    POSITION = "position"
    START = "start"
    WAIT = "wait"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MarlinResponse:
    """A parsed Marlin serial response line."""

    kind: MarlinResponseKind
    raw: str
    message: str | None = None
    resend_line: int | None = None
    action: str | None = None
    capability: tuple[str, bool] | None = None
    fields: Mapping[str, float] | None = None

    @property
    def is_ack(self) -> bool:
        """The line acknowledges a command (``ok``)."""
        return self.kind is MarlinResponseKind.OK

    @property
    def is_fatal(self) -> bool:
        """The controller has halted and needs a reset (``kill()``)."""
        return self.kind is MarlinResponseKind.HALT

    @property
    def needs_resend(self) -> bool:
        """The controller asked the host to resend from :attr:`resend_line`."""
        return self.kind is MarlinResponseKind.RESEND

    @property
    def is_keepalive(self) -> bool:
        """A busy/idle keepalive line carrying no command result."""
        return self.kind in (MarlinResponseKind.BUSY, MarlinResponseKind.WAIT)


# `Key:value` report fields (M105/M114/ADVANCED_OK), e.g. T:21.30, B@:0, X:1.50.
_REPORT_FIELD = re.compile(r"([A-Za-z@][\w@]*):(-?\d+(?:\.\d+)?)")
# Bare `LetterNumber` ADVANCED_OK fields, e.g. N12, P15, B3.
_ADVANCED_OK_FIELD = re.compile(r"^([A-Za-z])(-?\d+(?:\.\d+)?)$")

_BUSY = c.ECHO_PREFIX + c.BUSY_PREFIX  # "echo:busy:"


def _report_fields(text: str) -> dict[str, float]:
    return {m.group(1): float(m.group(2)) for m in _REPORT_FIELD.finditer(text)}


def _ok_fields(remainder: str) -> dict[str, float] | None:
    fields = _report_fields(remainder)  # M105 body carried on the ok line
    for token in remainder.split():  # ADVANCED_OK: ok N12 P15 B3
        match = _ADVANCED_OK_FIELD.fullmatch(token)
        if match:
            fields.setdefault(match.group(1), float(match.group(2)))
    return fields or None


def parse_response(line: str) -> MarlinResponse:
    """Classify a single received Marlin serial line.

    Prefixes are tested most-specific first (e.g. ``echo:busy:`` before
    ``echo:``, halt before generic ``Error:``) per the documented precedence.
    """
    stripped = line.strip()

    def result(kind: MarlinResponseKind, **extra: object) -> MarlinResponse:
        return MarlinResponse(kind=kind, raw=line, **extra)  # type: ignore[arg-type]

    if not stripped:
        return result(MarlinResponseKind.UNKNOWN)

    if stripped.startswith(c.ACTION_PREFIX):
        return result(MarlinResponseKind.ACTION, action=stripped[len(c.ACTION_PREFIX) :].strip())

    if stripped.startswith(c.RESEND):
        match = re.search(r"\d+", stripped)
        return result(
            MarlinResponseKind.RESEND,
            resend_line=int(match.group()) if match else None,
        )

    # Fatal/halt state (kill, thermal protection, stopped) — detect before the
    # generic Error branch; the host must stop streaming on any of these.
    if any(marker in stripped for marker in c.FATAL_MARKERS):
        message = (
            stripped[len(c.ERROR_PREFIX) :] if stripped.startswith(c.ERROR_PREFIX) else stripped
        )
        return result(MarlinResponseKind.HALT, message=message)

    if stripped.startswith(c.ERROR_PREFIX):
        return result(MarlinResponseKind.ERROR, message=stripped[len(c.ERROR_PREFIX) :])

    if stripped.startswith(_BUSY):
        return result(MarlinResponseKind.BUSY, message=stripped[len(_BUSY) :].strip())

    if stripped.startswith(c.ECHO_PREFIX):
        return result(MarlinResponseKind.ECHO, message=stripped[len(c.ECHO_PREFIX) :])

    if stripped.startswith(c.CAP_PREFIX):
        name, _, flag = stripped[len(c.CAP_PREFIX) :].rpartition(":")
        return result(MarlinResponseKind.CAPABILITY, capability=(name, flag == "1"))

    if stripped.startswith(c.FIRMWARE_PREFIX):
        return result(MarlinResponseKind.FIRMWARE, message=stripped)

    if stripped == c.OK or stripped.startswith(c.OK + " "):
        return result(MarlinResponseKind.OK, fields=_ok_fields(stripped[len(c.OK) :].strip()))

    # M105/M114 report bodies use field labels (temperature.cpp / motion.cpp),
    # not language.h literals.
    if stripped.startswith("T:"):
        return result(MarlinResponseKind.TEMPERATURE, fields=_report_fields(stripped))

    if stripped.startswith("X:") and "Count" in stripped:
        # Parse only the logical position, not the trailing `Count` stepper group.
        return result(
            MarlinResponseKind.POSITION,
            fields=_report_fields(stripped.split("Count", 1)[0]),
        )

    if stripped == c.START:
        return result(MarlinResponseKind.START)

    if stripped == c.WAIT:
        return result(MarlinResponseKind.WAIT)

    return result(MarlinResponseKind.UNKNOWN)
