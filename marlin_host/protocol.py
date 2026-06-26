"""Formal taxonomy and parser for Marlin host-facing serial responses.

A single pure function, :func:`parse_response`, classifies one received serial
line into a structured :class:`MarlinResponse`. This is the foundational
contract of the library: instead of ad-hoc string matching scattered across a
host implementation, every device->host line is mapped to one explicit taxonomy.

The taxonomy and the exact literals are grounded in the Marlin 2.1.x firmware
source (the binding host-protocol contract): ``ok``/ADVANCED_OK, ``Resend:``,
``Error:``, halt/``kill()``, ``echo:busy:`` keepalive, ``//action:``, M115
``Cap:``, M114/M105 reports, ``start``/``wait``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

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

    if stripped.startswith("//action:"):
        return result(MarlinResponseKind.ACTION, action=stripped[len("//action:") :].strip())

    if stripped.startswith("Resend:"):
        match = re.search(r"\d+", stripped)
        return result(
            MarlinResponseKind.RESEND,
            resend_line=int(match.group()) if match else None,
        )

    # Halt is a fatal Error: line; detect it before the generic Error branch.
    if "kill()" in stripped or "halted" in stripped.lower():
        message = stripped[len("Error:") :] if stripped.startswith("Error:") else stripped
        return result(MarlinResponseKind.HALT, message=message)

    if stripped.startswith("Error:"):
        return result(MarlinResponseKind.ERROR, message=stripped[len("Error:") :])

    if stripped.startswith("echo:busy:"):
        return result(MarlinResponseKind.BUSY, message=stripped[len("echo:busy:") :].strip())

    if stripped.startswith("echo:"):
        return result(MarlinResponseKind.ECHO, message=stripped[len("echo:") :])

    if stripped.startswith("Cap:"):
        name, _, flag = stripped[len("Cap:") :].rpartition(":")
        return result(MarlinResponseKind.CAPABILITY, capability=(name, flag == "1"))

    if stripped.startswith("FIRMWARE_NAME:"):
        return result(MarlinResponseKind.FIRMWARE, message=stripped)

    if stripped == "ok" or stripped.startswith("ok "):
        return result(MarlinResponseKind.OK, fields=_ok_fields(stripped[len("ok") :].strip()))

    if stripped.startswith("T:"):
        return result(MarlinResponseKind.TEMPERATURE, fields=_report_fields(stripped))

    if stripped.startswith("X:") and "Count" in stripped:
        # Parse only the logical position, not the trailing `Count` stepper group.
        return result(
            MarlinResponseKind.POSITION,
            fields=_report_fields(stripped.split("Count", 1)[0]),
        )

    if stripped == "start":
        return result(MarlinResponseKind.START)

    if stripped == "wait":
        return result(MarlinResponseKind.WAIT)

    return result(MarlinResponseKind.UNKNOWN)
