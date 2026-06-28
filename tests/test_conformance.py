"""Conformance corpus: replay real recorded sessions, hardware-free.

The ``.trace`` files in ``conformance/`` are captured from real Marlin hardware
(see that directory's README). Replaying them through ``FakeTransport.from_trace``
pins the host's modeled behavior against genuine wire output — so a regression in
parsing/negotiation/streaming is caught without a board attached. This is the
"replay" half of the capture→replay loop; the live capture lives in the
``@pytest.mark.hardware`` lane.
"""

from __future__ import annotations

from pathlib import Path

from marlin_host import FakeTransport, MarlinHost

CORPUS = Path(__file__).parent / "conformance"


def _load(name: str) -> list[str]:
    return (CORPUS / name).read_text().splitlines()


def test_connect_negotiates_the_real_ramps_profile() -> None:
    host = MarlinHost(FakeTransport.from_trace(_load("connect-ramps-2.1.x.trace")))
    host.connect()

    profile = host.profile
    assert profile is not None
    assert "Marlin bugfix-2.1.x" in profile.firmware
    # The genuine dialect of this RAMPS 1.4 build:
    assert profile.has("ARCS")
    assert profile.has("THERMAL_PROTECTION")
    assert not profile.has("EMERGENCY_PARSER")  # M112 is parsed in order, not immediate
    assert not profile.has("EEPROM")  # hardcoded defaults
    assert profile.advanced_ok is False  # bare `ok`, no `P..B..`
    assert profile.emergency_stop_immediate is False


def test_stream_replays_the_real_session() -> None:
    host = MarlinHost(FakeTransport.from_trace(_load("stream-ramps-2.1.x.trace")), reliable=True)
    host.connect()

    # Command text is cosmetic on replay (responses are positional), but the count
    # and shape must match the recorded groups: 11 commands, two of them M114.
    program = [
        "G90",
        "M82",
        "G21",
        "M114",
        "G4 P20",
        "G91",
        "G1 X0.2 F300",
        "G1 X-0.2 F300",
        "G90",
        "M400",
        "M114",
    ]
    progress = list(host.stream(program))

    assert len(progress) == len(program)
    assert all(p.response.is_ack for p in progress)
    assert progress[-1].commands_sent == len(program)
    assert progress[-1].total_commands == len(program)
