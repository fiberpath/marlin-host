"""Hardware lane: live validation against a real Marlin controller.

Skipped unless ``--port`` is given (see ``tests/conftest.py``). Safe to run with
motor/heater power OFF — these exercise the protocol/streaming/reliability path
at logic level (queries, framed sends, tiny relative moves, an M112 + recovery);
nothing depends on actual motion or heating.

    uv run pytest tests/hardware --port /dev/ttyACM0

Re-validates what the conformance corpus replays, and is how new traces are
captured (wrap the transport in TracingTransport with a file sink — see
working/bringup.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from marlin_host import HaltError, MarlinHost, SerialTransport

pytestmark = pytest.mark.hardware


@pytest.fixture
def rig(port: str, baud: int) -> Iterator[SimpleNamespace]:
    transport = SerialTransport(port, baud)  # reset_on_open -> DTR pulse resets the board
    host = MarlinHost(transport, reliable=True, startup_timeout=4.0)
    host.connect()
    try:
        yield SimpleNamespace(host=host, transport=transport)
    finally:
        # Leave the board in a clean, non-halted state for the next test.
        if host.is_halted:
            transport.reset(flush=True)
        host.close()


def test_connect_and_negotiate(rig: SimpleNamespace) -> None:
    assert rig.host.is_connected
    assert rig.host.profile is not None
    assert "Marlin" in rig.host.profile.firmware


def test_queries(rig: SimpleNamespace) -> None:
    assert rig.host.query("M114")  # at least the position report line
    assert rig.host.send("M105").is_ack  # temperature rides on the `ok` line


def test_reliable_stream(rig: SimpleNamespace) -> None:
    program = ["G91", "G1 X0.2 F300", "G1 X-0.2 F300", "G90", "M400"]
    progress = list(rig.host.stream(program))
    assert len(progress) == len(program)
    assert all(p.response.is_ack for p in progress)


def test_emergency_stop_then_recover(rig: SimpleNamespace) -> None:
    rig.host.emergency_stop()
    assert rig.host.is_halted
    with pytest.raises(HaltError):
        rig.host.send("M114")

    # Recovery path: flush the stale halt output, reset, reconnect (see #21).
    rig.transport.reset(flush=True)
    rig.host.connect()
    assert not rig.host.is_halted
    assert rig.host.profile is not None
