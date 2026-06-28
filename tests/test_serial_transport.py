"""Tests for SerialTransport's wrapper logic (pyserial mocked — no hardware).

The DTR-reset timing and real I/O behavior are validated against hardware
separately; here we only pin the line encoding/decoding and the Transport
contract.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from marlin_host import SerialTransport, Transport


def _fake_serial() -> MagicMock:
    fake = MagicMock()
    fake.readline.return_value = b"ok\n"
    return fake


def test_serial_transport_satisfies_transport_protocol() -> None:
    with patch("serial.serial_for_url", return_value=_fake_serial()):
        t = SerialTransport("loop://", reset_on_open=False)
    assert isinstance(t, Transport)


def test_write_line_appends_newline_and_flushes() -> None:
    fake = _fake_serial()
    with patch("serial.serial_for_url", return_value=fake):
        t = SerialTransport("loop://", reset_on_open=False)
    t.write_line("G28")
    fake.write.assert_called_once_with(b"G28\n")
    fake.flush.assert_called_once()


def test_read_line_decodes_and_strips_or_returns_none_on_timeout() -> None:
    fake = _fake_serial()
    with patch("serial.serial_for_url", return_value=fake):
        t = SerialTransport("loop://", reset_on_open=False)
    assert t.read_line() == "ok"
    fake.readline.return_value = b""  # timeout -> empty
    assert t.read_line(timeout=1.0) is None
    assert fake.timeout == 1.0


def test_reset_pulses_dtr_assert_then_release_without_flushing() -> None:
    events: list[tuple[str, object]] = []

    class _RecordingSerial:
        def __init__(self) -> None:
            self._dtr: bool | None = None

        @property
        def dtr(self) -> bool | None:
            return self._dtr

        @dtr.setter
        def dtr(self, value: bool) -> None:
            self._dtr = value
            events.append(("dtr", value))

        def reset_input_buffer(self) -> None:
            events.append(("flush", None))

        def close(self) -> None:
            events.append(("close", None))

    rec = _RecordingSerial()
    with patch("serial.serial_for_url", return_value=rec), patch("time.sleep") as sleep:
        t = SerialTransport("loop://", reset_on_open=True)

    # printrun sequence: assert (True) -> hold -> release (False); never left asserted.
    assert [v for kind, v in events if kind == "dtr"] == [True, False]
    # Don't flush — connect() needs to see the `start` greeting.
    assert ("flush", None) not in events
    sleep.assert_called_once()  # held between assert and release
    t.close()
    assert ("close", None) in events


def test_reset_with_flush_discards_stale_input_after_the_edge() -> None:
    """Recovery reset: flush stale pre-reset output (e.g. a post-M112 halt line)
    only *after* the DTR release, so it can't poison the next connect."""
    events: list[tuple[str, object]] = []

    class _RecordingSerial:
        @property
        def dtr(self) -> bool | None:
            return None

        @dtr.setter
        def dtr(self, value: bool) -> None:
            events.append(("dtr", value))

        def reset_input_buffer(self) -> None:
            events.append(("flush", None))

    rec = _RecordingSerial()
    with patch("serial.serial_for_url", return_value=rec), patch("time.sleep"):
        t = SerialTransport("loop://", reset_on_open=False)
        t.reset(flush=True)

    # Flush happens last — after assert(True) and release(False), never before.
    assert events == [("dtr", True), ("dtr", False), ("flush", None)]
