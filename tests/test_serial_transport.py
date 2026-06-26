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


def test_reset_toggles_dtr() -> None:
    fake = _fake_serial()
    with patch("serial.serial_for_url", return_value=fake), patch("time.sleep"):
        t = SerialTransport("loop://", reset_on_open=True)
    fake.reset_input_buffer.assert_called_once()
    assert fake.dtr is True  # left asserted after the low→high toggle
    t.close()
    fake.close.assert_called_once()
