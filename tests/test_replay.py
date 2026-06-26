"""Tests for replaying a captured session as a conformance fixture."""

from __future__ import annotations

from marlin_host import FakeTransport, MarlinHost, MarlinResponseKind, TracingTransport


def test_from_trace_replays_a_recorded_session() -> None:
    trace = [
        "< start",
        "> G28",
        "< echo:busy: processing",
        "< ok",
        "> M114",
        "< X:1.00 Y:2.00 Z:0.00 E:0.00 Count X:80 Y:160 Z:0",
        "< ok",
    ]
    host = MarlinHost(FakeTransport.from_trace(trace))
    host.connect()  # drains the leading `start`
    assert host.send("G28").is_ack

    report = host.query("M114")
    assert any(r.kind is MarlinResponseKind.POSITION for r in report)


def test_capture_then_replay_round_trip() -> None:
    def device(line: str) -> list[str]:
        if line == "M115":
            return ["FIRMWARE_NAME:Marlin 2.1.2.x", "Cap:EEPROM:1", "ok"]
        return ["ok"]

    # Capture a session via TracingTransport...
    log: list[str] = []
    captured = MarlinHost(TracingTransport(FakeTransport(responder=device), log.append))
    captured.send("G28")
    captured.capabilities()

    # ...then replay the captured log and drive a fresh host against it.
    replayed = MarlinHost(FakeTransport.from_trace(log))
    assert replayed.send("G28").is_ack
    assert replayed.capabilities().has("EEPROM")
