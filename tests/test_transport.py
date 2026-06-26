"""Tests for the transport abstraction and the in-memory FakeTransport."""

from __future__ import annotations

from marlin_host.transport import FakeTransport, Transport


def test_fake_transport_satisfies_protocol() -> None:
    assert isinstance(FakeTransport(), Transport)


def test_write_line_is_recorded() -> None:
    t = FakeTransport()
    t.write_line("G28")
    t.write_line("M114")
    assert t.written == ["G28", "M114"]


def test_fed_lines_are_read_in_order_then_timeout() -> None:
    t = FakeTransport()
    t.feed("start", "echo:busy: processing", "ok")
    assert t.read_line() == "start"
    assert t.read_line() == "echo:busy: processing"
    assert t.read_line() == "ok"
    assert t.read_line() is None  # nothing queued -> timeout


def test_responder_queues_replies_per_written_line() -> None:
    def responder(line: str) -> list[str]:
        return ["ok"] if line == "G28" else ["echo:Unknown command", "ok"]

    t = FakeTransport(responder=responder)
    t.write_line("G28")
    assert t.read_line() == "ok"
    assert t.read_line() is None

    t.write_line("G999")
    assert t.read_line() == "echo:Unknown command"
    assert t.read_line() == "ok"


def test_close_marks_closed() -> None:
    t = FakeTransport()
    assert not t.closed
    t.close()
    assert t.closed
