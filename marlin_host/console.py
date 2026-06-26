"""Interactive console for driving a Marlin controller.

``marlin-host --port /dev/ttyUSB0`` (after ``pip install 'marlin-host[serial]'``)
connects, prints the firmware/capabilities, then reads G-code from stdin. Special
commands: ``:caps``, ``:stream FILE``, ``:estop``, ``:quit``. ``--log FILE``
records the session (TX/RX) for the conformance corpus.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import TextIO

from .host import DEFAULT_IDLE_TIMEOUT, HostError, MarlinHost
from .transport import SerialTransport, TracingTransport, Transport

DEFAULT_BAUD = 250_000
_HELP = "type G-code, or :caps  :stream FILE  :estop  :quit"


def _dispatch(host: MarlinHost, raw: str, out: Callable[[str], None]) -> bool:
    """Handle one console line. Returns False to quit, True to keep going."""
    line = raw.strip()
    if not line:
        return True
    if line in (":quit", ":q", ":exit"):
        return False
    try:
        if line == ":estop":
            host.emergency_stop()
            out("** emergency stop sent (M112) **")
        elif line == ":caps":
            caps = host.capabilities()
            out(caps.firmware or "(no firmware line)")
            for name, enabled in sorted(caps.caps.items()):
                out(f"  {name}: {'yes' if enabled else 'no'}")
        elif line.startswith(":stream "):
            path = line[len(":stream ") :].strip()
            with open(path, encoding="utf-8") as handle:
                for progress in host.stream(handle):
                    out(f"  {progress.commands_sent}/{progress.total_commands}  {progress.command}")
            out("** stream complete **")
        elif line.startswith(":"):
            out(f"unknown command: {line}  ({_HELP})")
        else:
            resp = host.send(line)
            out(f"<- {resp.kind.value}: {resp.raw}")
    except HostError as exc:
        out(f"!! {exc}")
    return True


def _repl(
    host: MarlinHost, source: TextIO, out: Callable[[str], None], *, prompt: str = ""
) -> None:
    while True:
        if prompt:
            sys.stderr.write(prompt)
            sys.stderr.flush()
        line = source.readline()
        if not line:  # EOF
            return
        if not _dispatch(host, line, out):
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="marlin-host", description=__doc__)
    parser.add_argument("--port", required=True, help="serial port (e.g. /dev/ttyUSB0 or COM5)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--reliable", action="store_true", help="line-number + checksum mode")
    parser.add_argument("--no-reset", action="store_true", help="do not toggle DTR on open")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_IDLE_TIMEOUT, help="idle timeout (s)"
    )
    parser.add_argument("--log", help="record the session (TX/RX) to FILE for the corpus")
    args = parser.parse_args(argv)

    transport: Transport = SerialTransport(args.port, args.baud, reset_on_open=not args.no_reset)
    log_handle: TextIO | None = None
    if args.log:
        # noqa: SIM115 — the log handle lives for the whole session, closed in finally.
        log_handle = open(args.log, "w", encoding="utf-8")  # noqa: SIM115
        sink = log_handle
        transport = TracingTransport(transport, lambda s: print(s, file=sink, flush=True))

    host = MarlinHost(transport, reliable=args.reliable, idle_timeout=args.timeout)
    print(f"connecting to {args.port} @ {args.baud} ...", file=sys.stderr)
    host.connect()
    try:
        print(host.capabilities().firmware or "(connected)", file=sys.stderr)
    except HostError as exc:
        print(f"(M115 failed: {exc})", file=sys.stderr)
    print(_HELP, file=sys.stderr)

    try:
        _repl(host, sys.stdin, print, prompt="marlin> ")
    finally:
        host.close()
        if log_handle is not None:
            log_handle.close()
    return 0
