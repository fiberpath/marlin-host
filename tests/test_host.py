"""Tests for MarlinHost driven entirely through FakeTransport (no hardware)."""

from __future__ import annotations

import pytest

from marlin_host import FakeTransport, MarlinHost, Profile
from marlin_host import _constants as c
from marlin_host.framing import checksum
from marlin_host.host import HaltError, HostError, ProtocolError


class _ResendSim:
    """Minimal stateful Marlin double: a content-dependent ``FakeTransport`` responder.

    Validates each framed line's ``N`` + XOR checksum and emits Marlin's real
    recovery — ``Error:`` then ``Resend: N`` then a trailing ``ok`` (the resend
    *request* ack, ``queue.cpp:275-276``) — followed, once the host resends, by the
    resent line's real ``ok``. Optionally injects one transmission error to drive a
    recovery. Unlike ``from_trace`` (indexed by write count, ``transport.py:75``),
    this reacts to the exact bytes written, so it can prove checksum/line-number
    correctness and expose the request-ack desync.
    """

    def __init__(self, *, corrupt_first: int = 0) -> None:
        self.expected = 1
        self._corrupt_pending = int(corrupt_first)

    def __call__(self, line: str) -> list[str]:
        head, _, csum = line.partition("*")
        number = int(head[1:].split(" ", 1)[0])
        if "M110" in line:  # framed resync sets the next expected line number
            self.expected = number + 1
            return ["ok"]
        valid = checksum(head) == int(csum) and number == self.expected
        inject = self._corrupt_pending > 0
        if inject:
            self._corrupt_pending -= 1
        if inject or not valid:
            last = self.expected - 1
            return [
                f"Error:checksum mismatch, Last Line: {last}",
                f"Resend: {self.expected}",
                "ok",  # request ack, BEFORE the resent line is processed
            ]
        self.expected += 1
        return ["ok"]


def test_connect_consumes_startup_banner_until_idle() -> None:
    # Fresh boot: `start` is the positive ready signal; drain the rest of the
    # banner (Marlin emits the version line bare, no `echo:` prefix).
    t = FakeTransport()
    t.feed("start", "Marlin 2.1.2.x", "echo: Free Memory: 4096")
    host = MarlinHost(t)
    host.connect()
    assert host.is_connected


def test_connect_returns_ready_on_banner_then_wait_without_probing() -> None:
    # Booted then idle: `start` + a `wait` keepalive is enough — no probe needed.
    t = FakeTransport()
    t.feed("start", "wait")
    host = MarlinHost(t)
    host.connect()
    assert host.is_connected
    assert not any("M110" in w for w in t.written)


def test_connect_probes_idle_board_streaming_wait() -> None:
    # An already-running idle board emits `wait` keepalives (NO_TIMEOUTS) with no
    # boot banner. The host must treat that as a sign of life and probe for a
    # definitive `ok` rather than spinning in the banner-drain loop forever.
    t = FakeTransport(responder=lambda line: ["ok"] if "M110" in line else [])
    t.feed("wait")
    host = MarlinHost(t)
    host.connect()
    assert host.is_connected
    assert any("M110 N0" in w for w in t.written)


def test_connect_probes_already_running_board() -> None:
    # No boot banner (board was already up / ignored DTR): the framed M110 hello
    # must elicit an `ok` for the host to consider it ready.
    t = FakeTransport(responder=lambda line: ["ok"] if "M110" in line else [])
    host = MarlinHost(t)
    host.connect()
    assert host.is_connected
    assert any("M110 N0" in w for w in t.written)


def test_connect_raises_when_controller_unresponsive() -> None:
    # Nothing on the wire and no answer to the probe -> fail loudly, not "ready".
    t = FakeTransport()
    host = MarlinHost(t, connect_probes=2)
    with pytest.raises(HostError):
        host.connect()
    assert not host.is_connected


def test_connect_detects_halt_during_boot() -> None:
    t = FakeTransport()
    t.feed("Error:Printer halted. kill() called!")
    host = MarlinHost(t)
    with pytest.raises(HaltError):
        host.connect()
    assert host.is_halted


def _m115_responder(*caps: str, ok: str = "ok", firmware: str = "FIRMWARE_NAME:Marlin 2.1.2.x"):
    """Responder that answers M115 with a firmware/cap block and probes with `ok`."""

    def responder(line: str) -> list[str]:
        if line == "M115":
            return [firmware, *caps, ok]
        return ["ok"] if "M110" in line else []

    return responder


def test_connect_negotiates_profile_from_m115() -> None:
    t = FakeTransport(_m115_responder("Cap:EMERGENCY_PARSER:1", "Cap:AUTOREPORT_TEMP:0"))
    t.feed("start", "wait")  # ready via banner; negotiation then queries M115
    host = MarlinHost(t)
    host.connect()
    assert host.profile is not None
    assert "Marlin" in host.profile.firmware
    assert host.profile.has("EMERGENCY_PARSER")
    assert host.profile.emergency_stop_immediate
    assert not host.profile.has("AUTOREPORT_TEMP")  # reported disabled -> off
    assert not host.profile.has("SDCARD")  # never reported (sparse report) -> off


def test_connect_infers_advanced_ok_from_ok_fields() -> None:
    t = FakeTransport(_m115_responder(ok="ok N0 P15 B4"))  # ADVANCED_OK form
    t.feed("start", "wait")
    host = MarlinHost(t)
    host.connect()
    assert host.profile is not None and host.profile.advanced_ok


def test_connect_infers_no_advanced_ok_from_bare_ok() -> None:
    t = FakeTransport(_m115_responder())  # bare `ok`
    t.feed("start", "wait")
    host = MarlinHost(t)
    host.connect()
    assert host.profile is not None and not host.profile.advanced_ok


def test_connect_advanced_ok_override_beats_inference() -> None:
    t = FakeTransport(_m115_responder())  # board emits bare `ok`...
    t.feed("start", "wait")
    host = MarlinHost(t)
    host.connect(advanced_ok=True)  # ...but the caller declares ADVANCED_OK
    assert host.profile is not None and host.profile.advanced_ok


def test_connect_advanced_ok_false_override_suppresses_inference() -> None:
    t = FakeTransport(_m115_responder(ok="ok N0 P15 B4"))  # board emits ADVANCED_OK...
    t.feed("start", "wait")
    host = MarlinHost(t)
    host.connect(advanced_ok=False)  # ...but the caller forces it off
    assert host.profile is not None and not host.profile.advanced_ok


def test_connect_emits_wait_when_idle_is_declared() -> None:
    t = FakeTransport(_m115_responder())
    t.feed("start", "wait")
    host = MarlinHost(t)
    host.connect(emits_wait_when_idle=True)
    assert host.profile is not None and host.profile.emits_wait_when_idle


def test_connect_tolerates_silent_m115() -> None:
    # Ready via the M110 probe, but the board never answers M115: empty profile,
    # still connected (readiness is already proven — don't fail the connection).
    t = FakeTransport(responder=lambda line: ["ok"] if "M110" in line else [])
    host = MarlinHost(t)
    host.connect()
    assert host.is_connected
    assert host.profile is not None
    assert host.profile.firmware == ""
    assert not host.profile.has("EEPROM")


def test_connect_negotiate_false_skips_m115() -> None:
    t = FakeTransport()
    t.feed("start", "wait")
    host = MarlinHost(t)
    host.connect(negotiate=False)
    assert host.is_connected
    assert host.profile is None
    assert not any("M115" in w for w in t.written)


def test_connect_raises_halt_when_m115_reports_kill() -> None:
    def responder(line: str) -> list[str]:
        return ["Error:Printer halted. kill() called!"] if line == "M115" else []

    t = FakeTransport(responder)
    t.feed("start", "wait")
    host = MarlinHost(t)
    with pytest.raises(HaltError):
        host.connect()
    assert host.is_halted
    assert not host.is_connected


# --- dialect matrix: one Profile drives both the host and the device double ----


def _dialect_device(profile: Profile):
    """A Profile-driven Marlin double (``FakeTransport`` responder).

    Answers M115 in the profile's dialect and acks every command in its ``ok``
    form (bare, or ADVANCED_OK's ``ok P.. B..``), with an optional idle ``wait``
    heartbeat ahead of each reply. Literals come from :mod:`marlin_host._constants`,
    so the double and the host stay bound to the same generated contract — the
    seam read from both sides.
    """

    def ok() -> str:
        # ADVANCED_OK appends planner/block buffer counts; N is omitted for a bare
        # (unnumbered) command, matching Marlin's ok_to_send (queue.cpp).
        return f"{c.OK} P15 B4" if profile.advanced_ok else c.OK

    def responder(line: str) -> list[str]:
        if line == "M115":
            caps = [f"{c.CAP_PREFIX}{n}:{'1' if on else '0'}" for n, on in profile.caps.items()]
            return [f"{c.FIRMWARE_PREFIX}{profile.firmware}", *caps, ok()]
        wait = [c.WAIT] if profile.emits_wait_when_idle else []
        return [*wait, ok()]

    return responder


@pytest.mark.parametrize("advanced_ok", [False, True])
@pytest.mark.parametrize("emits_wait_when_idle", [False, True])
@pytest.mark.parametrize(
    "caps",
    [{}, {"EMERGENCY_PARSER": True, "AUTOREPORT_TEMP": False}],
    ids=["no-caps", "caps"],
)
def test_dialect_matrix_connect_and_stream(
    advanced_ok: bool, emits_wait_when_idle: bool, caps: dict[str, bool]
) -> None:
    profile = Profile(
        firmware="Marlin 2.1.2.x",
        caps=caps,
        advanced_ok=advanced_ok,
        emits_wait_when_idle=emits_wait_when_idle,
    )
    t = FakeTransport(_dialect_device(profile))
    t.feed("start", *(["wait"] if emits_wait_when_idle else []))
    host = MarlinHost(t)
    host.connect(emits_wait_when_idle=emits_wait_when_idle)

    # The host resolved the same dialect it was driven with (advanced_ok inferred
    # from the M115 ok; caps negotiated; wait-when-idle declared).
    assert host.profile is not None
    assert host.profile.advanced_ok is advanced_ok
    assert host.profile.emits_wait_when_idle is emits_wait_when_idle
    for name, enabled in caps.items():
        assert host.profile.has(name) is enabled

    # A short stream completes regardless of dialect — same host code, and the
    # `wait` heartbeat is consumed without desyncing the ok-per-command pacing.
    results = list(host.stream(["G28", "G1 X10", "G1 X20"]))
    assert [r.commands_sent for r in results] == [1, 2, 3]
    assert all(r.response.is_ack for r in results)


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
            # Marlin's real recovery: Error, Resend, then a trailing `ok` that acks
            # the resend request (queue.cpp:275-276) — not the resent line.
            return ["Error:checksum mismatch, Last Line: 0", "Resend: 1", "ok"]
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


def test_reliable_send_returns_resent_lines_ok_not_request_ack() -> None:
    # Regression for the resend desync: on recovery Marlin emits a trailing `ok`
    # (the resend-request ack) before the resent line's real `ok`. The host must
    # swallow the request ack and return the real one, leaving the stream in sync.
    sim = _ResendSim(corrupt_first=1)
    t = FakeTransport(responder=sim)
    host = MarlinHost(t, reliable=True)

    assert host.send("G1 X10").is_ack  # line 1: corrupted once, then recovered
    assert host.send("G1 X20").is_ack  # line 2: must not consume a stale ack

    # In sync: the controller advanced to line 3 and no unconsumed `ok` is left in
    # the stream (the off-by-one bug leaks the resent line's real `ok` to here).
    assert sim.expected == 3
    assert t.read_line() is None


def test_reliable_send_recovers_from_back_to_back_resends() -> None:
    # Two consecutive recoveries before success: each Resend's request-ack must be
    # swallowed independently (the flag re-arms per resend), then the resent line's
    # real `ok` returned — proving the boolean flag survives back-to-back recovery.
    sim = _ResendSim(corrupt_first=2)
    t = FakeTransport(responder=sim)
    host = MarlinHost(t, reliable=True)

    assert host.send("G1 X10").is_ack  # rejected twice, accepted on the third frame
    assert host.send("G1 X20").is_ack
    assert sim.expected == 3
    assert t.read_line() is None


def test_temperatures_reads_fields_from_the_ok_line() -> None:
    # Marlin answers M105 on the `ok` line itself (real RAMPS: `ok T:20.63 /0.00 @:0`),
    # so the temps are terminal-response fields, not a preceding report line.
    host = MarlinHost(FakeTransport(responder=lambda _line: ["ok T:20.63 /0.00 @:0"]))
    temps = host.temperatures()
    assert temps["T"] == pytest.approx(20.63)
    assert temps["@"] == pytest.approx(0.0)


def test_temperatures_empty_when_board_reports_none() -> None:
    host = MarlinHost(FakeTransport(responder=lambda _line: ["ok"]))
    assert host.temperatures() == {}


def test_on_action_delivers_board_initiated_host_actions() -> None:
    # //action:pause (e.g. M600/runout/LCD) arrives mid-command; without a
    # callback it is silently dropped, so the consumer must be able to observe it.
    seen: list[str] = []
    host = MarlinHost(
        FakeTransport(responder=lambda _line: ["//action:pause", "ok"]),
        on_action=lambda r: seen.append(r.action or r.raw),
    )
    assert host.send("G1 X10").is_ack
    assert seen == ["pause"]


def test_actions_without_a_callback_are_dropped_not_fatal() -> None:
    host = MarlinHost(FakeTransport(responder=lambda _line: ["//action:cancel", "ok"]))
    assert host.send("G1 X10").is_ack  # no callback registered -> no error


def test_on_action_fires_during_streaming() -> None:
    seen: list[str] = []
    replies = iter([["ok"], ["//action:paused", "ok"], ["ok"]])
    host = MarlinHost(
        FakeTransport(responder=lambda _line: next(replies)),
        on_action=lambda r: seen.append(r.action or r.raw),
    )
    list(host.stream(["G1 X1", "G1 X2", "G1 X3"]))
    assert seen == ["paused"]


def test_position_reads_the_m114_report() -> None:
    host = MarlinHost(
        FakeTransport(
            responder=lambda _line: ["X:1.00 Y:2.00 Z:0.00 E:0.00 Count X:80 Y:160 Z:0", "ok"]
        )
    )
    pos = host.position()
    assert pos["X"] == pytest.approx(1.0)
    assert pos["Y"] == pytest.approx(2.0)
    assert "Count" not in pos  # the machine-step tail is dropped by the parser


def test_position_empty_when_no_report_line() -> None:
    host = MarlinHost(FakeTransport(responder=lambda _line: ["ok"]))
    assert host.position() == {}


def test_send_and_query_are_mutually_exclusive_across_threads() -> None:
    # The io lock must serialise transport access so a worker streaming and
    # another thread's manual command never interleave on the wire.
    import threading
    import time as _time

    ops: list[str] = []

    class _SlowTransport:
        def write_line(self, line: str) -> None:
            ops.append(f"w:{line}")

        def read_line(self, timeout: float | None = None) -> str | None:
            ops.append("r0")
            _time.sleep(0.05)
            ops.append("r1")
            return "ok"

        def close(self) -> None:
            pass

    host = MarlinHost(_SlowTransport())
    worker = threading.Thread(target=lambda: host.send("A"))
    worker.start()
    _time.sleep(0.01)  # let A enter the locked region first
    host.send("B")
    worker.join()

    # Each send is [w:X, r0, r1]; the lock keeps the two triples contiguous.
    # Without it, the 0.05s read overlap would interleave them.
    assert len(ops) == 6
    assert ops[1:3] == ["r0", "r1"]
    assert ops[4:6] == ["r0", "r1"]
    assert ops[0].startswith("w:") and ops[3].startswith("w:")
