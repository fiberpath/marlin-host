"""Tests for the console command dispatch + REPL (no real serial)."""

from __future__ import annotations

import io
from collections.abc import Callable

from marlin_host import FakeTransport, MarlinHost
from marlin_host.console import _dispatch, _repl


def _host(responder: Callable[[str], list[str]] | None = None) -> MarlinHost:
    return MarlinHost(FakeTransport(responder=responder or (lambda _line: ["ok"])))


def test_dispatch_send_prints_response() -> None:
    out: list[str] = []
    assert _dispatch(_host(), "G28", out.append) is True
    assert out == ["<- ok: ok"]


def test_dispatch_quit_returns_false() -> None:
    assert _dispatch(_host(), ":quit", lambda _s: None) is False


def test_dispatch_blank_is_noop() -> None:
    out: list[str] = []
    assert _dispatch(_host(), "   ", out.append) is True
    assert out == []


def test_dispatch_estop_halts_and_reports() -> None:
    host = _host()
    out: list[str] = []
    _dispatch(host, ":estop", out.append)
    assert host.is_halted
    assert any("emergency stop" in line for line in out)


def test_dispatch_caps_lists_capabilities() -> None:
    def responder(line: str) -> list[str]:
        if line == "M115":
            return ["FIRMWARE_NAME:Marlin 2.1.2.x", "Cap:EEPROM:1", "ok"]
        return ["ok"]

    out: list[str] = []
    _dispatch(_host(responder), ":caps", out.append)
    assert any("Marlin" in line for line in out)
    assert any("EEPROM: yes" in line for line in out)


def test_dispatch_catches_host_error() -> None:
    host = MarlinHost(FakeTransport())  # nothing queued -> send raises HostError
    out: list[str] = []
    assert _dispatch(host, "G28", out.append) is True
    assert any(line.startswith("!!") for line in out)


def test_dispatch_unknown_command() -> None:
    out: list[str] = []
    _dispatch(_host(), ":bogus", out.append)
    assert any("unknown command" in line for line in out)


def test_repl_runs_until_eof() -> None:
    out: list[str] = []
    _repl(_host(), io.StringIO("G28\nG1 X10\n"), out.append)
    assert len([line for line in out if line.startswith("<- ok")]) == 2


def test_repl_stops_on_quit() -> None:
    out: list[str] = []
    _repl(_host(), io.StringIO("G28\n:quit\nG1 X10\n"), out.append)
    assert len([line for line in out if line.startswith("<- ok")]) == 1
