# marlin-host

A host-side Python library for the **Marlin** firmware serial protocol — the
PC/host end of the line-numbered, checksummed, `ok`-acknowledged G-code
conversation that hosts like OctoPrint and Pronterface speak to a printer or
CNC running Marlin. Neutral and standalone; not tied to any one application.

> **Status:** MVP. Connect, M115 negotiation, reliable framed send, streaming,
> queries, and out-of-band e-stop are implemented and **validated against real
> hardware** (RAMPS 1.4 / Marlin `bugfix-2.1.x`, logic level — motion/thermal
> validation under power is ongoing). Developed against the Marlin firmware
> source and official docs. Licensed Apache-2.0.

## Repository conventions

A few top-level directories are **gitignored** (local-only) and used while
developing — they are intentionally *not* version-controlled:

- **`working/`** — scratch material we generate: protocol references, design
  notes, research write-ups. Local working files, not shipped.
- **`_reference/`** — external material pulled in to build against: Marlin
  firmware source, docs, and other technical references. Third-party, large,
  and in flux, so kept out of the repo.
- **`planning/`** — planning notes and task breakdowns.

Anything intended to ship — the library, its tests, and curated docs — lives in
tracked directories, never in `working/`, `_reference/`, or `planning/`.

## Develop

Requires [`uv`](https://docs.astral.sh/uv/) and [`just`](https://just.systems/).

```sh
just setup        # uv sync
just check        # ruff format-check + lint + pyright (CI-equivalent)
just test         # pytest
```

## Usage

Parse a single response line:

```python
from marlin_host import parse_response

resp = parse_response("ok N12 P15 B3")
resp.is_ack          # True
resp.fields          # {'N': 12.0, 'P': 15.0, 'B': 3.0}

parse_response("Resend: 13").resend_line     # 13
parse_response("echo:busy: processing").is_keepalive   # True
```

Drive a real controller (`pip install 'marlin-host[serial]'`):

```python
from marlin_host import MarlinHost, SerialTransport

host = MarlinHost(SerialTransport("/dev/ttyUSB0", 250000), reliable=True)
host.connect()                       # resets the board, drains the startup banner
print(host.capabilities().firmware)

host.send("G28")                     # homes; returns when the controller acks `ok`
for progress in host.stream(open("part.gcode")):
    print(f"{progress.commands_sent}/{progress.total_commands}")

host.emergency_stop()                # M112, out-of-band
```

Develop and test against `FakeTransport` — no hardware required.

## Console

An interactive console for hands-on bring-up:

```sh
marlin-host --port /dev/ttyUSB0 --reliable --log session.trace
# then type G-code, or :caps  :stream part.gcode  :estop  :quit
```

`--log` records every TX/RX line (via `TracingTransport`) — a captured session
that can seed the conformance corpus.
