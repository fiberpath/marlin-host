"""Tests for MarlinHost driven entirely through FakeTransport (no hardware)."""

from __future__ import annotations

import pytest

from marlin_host import FakeTransport, MarlinHost
from marlin_host.host import HaltError, HostError, ProtocolError


def test_connect_consumes_startup_banner_until_idle() -> None:
    t = FakeTransport()
    t.feed("start", "echo:Marlin 2.1.2.x", "echo:SD card ok")
    host = MarlinHost(t)
    host.connect()
    assert host.is_connected


def test_send_returns_ok_and_writes_command() -> None:
    t = FakeTransport(responder=lambda _line: ["ok"])
    host = MarlinHost(t)
    resp = host.send("G28")
    assert resp.is_ack
    assert t.written == ["G28"]


def test_send_consumes_busy_keepalive_then_ok() -> None:
    t = FakeTransport(
        responder=lambda _line: ["echo:busy: processing", "echo:busy: processing", "ok"]
    )
    host = MarlinHost(t)
    assert host.send("G1 X10").is_ack


def test_send_raises_on_paused_for_user_busy() -> None:
    # A bare send must not block forever on an M0-style user pause.
    t = FakeTransport(responder=lambda _line: ["echo:busy: paused for user"])
    host = MarlinHost(t)
    with pytest.raises(HostError):
        host.send("M0")


def test_send_raises_when_unresponsive() -> None:
    t = FakeTransport()  # nothing queued -> read_line returns None (timeout)
    host = MarlinHost(t)
    with pytest.raises(HostError):
        host.send("G28")


def test_send_raises_halt_on_fatal_thermal() -> None:
    t = FakeTransport(
        responder=lambda _line: ["Error:Thermal Runaway, system stopped! Heater_ID: 0"]
    )
    host = MarlinHost(t)
    with pytest.raises(HaltError):
        host.send("M104 S250")
    assert host.is_halted


def test_send_raises_protocol_error_on_generic_error() -> None:
    t = FakeTransport(responder=lambda _line: ["Error:something went wrong"])
    host = MarlinHost(t)
    with pytest.raises(ProtocolError):
        host.send("G1 X10")


def test_emergency_stop_writes_m112_out_of_band_and_halts() -> None:
    t = FakeTransport(responder=lambda _line: ["ok"])
    host = MarlinHost(t)
    host.emergency_stop()
    assert "M112" in t.written
    assert host.is_halted
    with pytest.raises(HaltError):
        host.send("G28")


def test_reliable_send_frames_with_line_number() -> None:
    t = FakeTransport(responder=lambda _line: ["ok"])
    host = MarlinHost(t, reliable=True)
    host.send("G28")
    assert t.written[0].startswith("N1 G28*")


def test_reliable_send_retransmits_on_resend_then_succeeds() -> None:
    state = {"n": 0}

    def responder(_line: str) -> list[str]:
        state["n"] += 1
        if state["n"] == 1:
            return ["Error:checksum mismatch, Last Line: 0", "Resend: 1"]
        return ["ok"]

    t = FakeTransport(responder=responder)
    host = MarlinHost(t, reliable=True)
    host.send("G28")
    # Original send + one retransmit, both framed for line 1.
    assert [w for w in t.written if w.startswith("N1 G28*")] != []
    assert len(t.written) == 2


def test_reliable_send_gives_up_after_max_resends() -> None:
    t = FakeTransport(
        responder=lambda _line: ["Error:checksum mismatch, Last Line: 0", "Resend: 1"]
    )
    host = MarlinHost(t, reliable=True, max_resends=3)
    with pytest.raises(ProtocolError):
        host.send("G28")
