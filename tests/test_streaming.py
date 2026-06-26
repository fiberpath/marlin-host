"""Tests for MarlinHost streaming + capability query (hardware-free)."""

from __future__ import annotations

import threading
import time

from marlin_host import Capabilities, FakeTransport, MarlinHost, StreamProgress


def test_stream_sends_every_command_and_yields_progress() -> None:
    t = FakeTransport(responder=lambda _line: ["ok"])
    host = MarlinHost(t)
    progress = list(host.stream(["G28", "G1 X10", "G1 X20"]))

    assert [p.command for p in progress] == ["G28", "G1 X10", "G1 X20"]
    assert progress[-1] == StreamProgress(3, 3, "G1 X20", progress[-1].response)
    assert t.written == ["G28", "G1 X10", "G1 X20"]


def test_stream_skips_blanks_and_comments() -> None:
    t = FakeTransport(responder=lambda _line: ["ok"])
    host = MarlinHost(t)
    program = ["; header", "", "  G28  ", "; move", "G1 X10", "   "]
    progress = list(host.stream(program))

    assert t.written == ["G28", "G1 X10"]
    assert progress[0].total_commands == 2


def test_stop_ends_stream_early() -> None:
    t = FakeTransport(responder=lambda _line: ["ok"])
    host = MarlinHost(t)
    seen = []
    for p in host.stream(["G1 X1", "G1 X2", "G1 X3"]):
        seen.append(p)
        if p.commands_sent == 1:
            host.stop()

    assert len(seen) == 1
    assert t.written == ["G1 X1"]


def test_pause_blocks_then_resume_completes() -> None:
    t = FakeTransport(responder=lambda _line: ["ok"])
    host = MarlinHost(t)
    host.pause()
    out: list[StreamProgress] = []

    worker = threading.Thread(
        target=lambda: out.extend(host.stream(["G1 X1", "G1 X2"], poll_interval=0.01)),
        daemon=True,
    )
    worker.start()

    # Paused: nothing should be sent within a short window.
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline and not t.written:
        time.sleep(0.01)
    assert t.written == []

    host.resume()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert len(out) == 2


def test_capabilities_parses_m115() -> None:
    def responder(line: str) -> list[str]:
        if line == "M115":
            return [
                "FIRMWARE_NAME:Marlin 2.1.2.x (Sep 12 2024) PROTOCOL_VERSION:1.0",
                "Cap:EEPROM:1",
                "Cap:AUTOREPORT_TEMP:0",
                "Cap:EMERGENCY_PARSER:1",
                "ok",
            ]
        return ["ok"]

    host = MarlinHost(FakeTransport(responder=responder))
    caps = host.capabilities()

    assert isinstance(caps, Capabilities)
    assert "Marlin" in caps.firmware
    assert caps.has("EEPROM")
    assert caps.has("EMERGENCY_PARSER")
    assert not caps.has("AUTOREPORT_TEMP")
    assert not caps.has("NONEXISTENT")
