"""Tests for TracingTransport (session capture)."""

from __future__ import annotations

from marlin_host import FakeTransport, TracingTransport, Transport


def test_tracing_logs_tx_and_rx_in_order() -> None:
    inner = FakeTransport(responder=lambda _line: ["echo:busy: processing", "ok"])
    log: list[str] = []
    t = TracingTransport(inner, log.append)

    assert isinstance(t, Transport)
    t.write_line("G28")
    assert t.read_line() == "echo:busy: processing"
    assert t.read_line() == "ok"
    assert t.read_line() is None  # timeout not logged

    assert log == ["> G28", "< echo:busy: processing", "< ok"]
    assert inner.written == ["G28"]  # delegated to the inner transport


def test_tracing_close_delegates() -> None:
    inner = FakeTransport()
    TracingTransport(inner, lambda _s: None).close()
    assert inner.closed
