"""Tests for the Marlin response taxonomy + parser.

Cases use exact source-verified literals from the Marlin 2.1.x firmware. The
parser is a pure function over a single received line.
"""

from __future__ import annotations

import pytest

from marlin_host.protocol import MarlinResponseKind as Kind
from marlin_host.protocol import parse_response


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("ok", Kind.OK),
        ("ok N12 P15 B15", Kind.OK),
        ("ok T:21.30 /0.00 B:21.50 /0.00 @:0 B@:0", Kind.OK),
        ("Resend: 13", Kind.RESEND),
        ("Error:checksum mismatch, Last Line: 12", Kind.ERROR),
        ("Error:Line Number is not Last Line Number+1, Last Line: 40", Kind.ERROR),
        ("Error:No Checksum with line number, Last Line: 7", Kind.ERROR),
        ("Error:Printer halted. kill() called!", Kind.HALT),
        ("Thermal Runaway, system stopped! Heater_ID: 0", Kind.HALT),
        ("Error:MINTEMP triggered, system stopped! Heater_ID: 0", Kind.HALT),
        ("!! KILL caused by KILL button/pin", Kind.HALT),
        (
            "Error:Printer stopped due to errors. Fix the error and use M999 to "
            "restart. (Temperature is reset. Set it after restarting)",
            Kind.HALT,
        ),
        ("echo:busy: processing", Kind.BUSY),
        ("echo:busy: paused for user", Kind.BUSY),
        ("//action:pause", Kind.ACTION),
        ("//action:prompt_begin Heating...", Kind.ACTION),
        ('echo:Unknown command:"G999"', Kind.ECHO),
        ("echo:  M92 X80.00 Y80.00 Z400.00 E93.00", Kind.ECHO),
        ("Cap:EEPROM:1", Kind.CAPABILITY),
        ("Cap:PROMPT_SUPPORT:0", Kind.CAPABILITY),
        ("FIRMWARE_NAME:Marlin 2.1.2.4 (Sep 12 2024) PROTOCOL_VERSION:1.0", Kind.FIRMWARE),
        ("X:0.00 Y:0.00 Z:0.00 E:0.00 Count X:0 Y:0 Z:0", Kind.POSITION),
        ("T:21.30 /0.00 B:21.50 /0.00 @:0 B@:0", Kind.TEMPERATURE),
        ("start", Kind.START),
        ("wait", Kind.WAIT),
        ("", Kind.UNKNOWN),
        ("some unrecognized banner line", Kind.UNKNOWN),
    ],
)
def test_classification(line: str, kind: Kind) -> None:
    assert parse_response(line).kind is kind


def test_raw_is_preserved_verbatim() -> None:
    raw = "  ok N12 P15 B15  "
    assert parse_response(raw).raw == raw


def test_ok_is_ack_and_not_keepalive() -> None:
    resp = parse_response("ok")
    assert resp.is_ack
    assert not resp.is_keepalive
    assert not resp.is_fatal


def test_advanced_ok_fields_parsed() -> None:
    resp = parse_response("ok N12 P15 B3")
    assert resp.is_ack
    assert resp.fields == {"N": 12.0, "P": 15.0, "B": 3.0}


def test_ok_with_m105_body_is_ack_and_exposes_temperatures() -> None:
    resp = parse_response("ok T:21.30 /0.00 B:21.50 /0.00 @:0 B@:0")
    assert resp.is_ack
    assert resp.fields is not None
    assert resp.fields["T"] == pytest.approx(21.30)
    assert resp.fields["B"] == pytest.approx(21.50)


def test_resend_line_parsed() -> None:
    resp = parse_response("Resend: 13")
    assert resp.needs_resend
    assert resp.resend_line == 13


def test_busy_is_keepalive() -> None:
    assert parse_response("echo:busy: processing").is_keepalive
    assert parse_response("wait").is_keepalive


def test_halt_is_fatal_and_beats_generic_error() -> None:
    resp = parse_response("Error:Printer halted. kill() called!")
    assert resp.kind is Kind.HALT
    assert resp.is_fatal


def test_action_verb_extracted() -> None:
    assert parse_response("//action:pause").action == "pause"
    assert parse_response("//action:notification Heating bed").action == "notification Heating bed"


def test_capability_name_and_flag() -> None:
    assert parse_response("Cap:EEPROM:1").capability == ("EEPROM", True)
    assert parse_response("Cap:PROMPT_SUPPORT:0").capability == ("PROMPT_SUPPORT", False)


def test_position_fields_parsed() -> None:
    resp = parse_response("X:1.50 Y:2.00 Z:0.00 E:0.00 Count X:120 Y:160 Z:0")
    assert resp.kind is Kind.POSITION
    assert resp.fields is not None
    assert resp.fields["X"] == pytest.approx(1.50)


def test_error_message_preserved() -> None:
    resp = parse_response("Error:checksum mismatch, Last Line: 12")
    assert resp.kind is Kind.ERROR
    assert resp.message == "checksum mismatch, Last Line: 12"
