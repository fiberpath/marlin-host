# Conformance corpus

Recorded host↔device sessions captured from **real Marlin hardware**, replayed
hardware-free by `tests/test_conformance.py` through `FakeTransport.from_trace`.
Each line is `> tx` (host→device) or `< rx` (device→host) — the format
`TracingTransport` emits, so a captured session drops straight in here.

These pin the modeled behavior against genuine wire output: a regression in
parsing, M115 negotiation, or streaming fails the replay with no board attached.

## Fixtures

| File | Source | Exercises |
|------|--------|-----------|
| `connect-ramps-2.1.x.trace` | RAMPS 1.4, Marlin `bugfix-2.1.x` (Jan 2026), 250000 baud, no 12V | connect: `start` banner → M115 negotiation → real capability set (`EMERGENCY_PARSER:0`, `EEPROM:0`, `ARCS:1`, …) |
| `stream-ramps-2.1.x.trace` | same board | connect + a framed stream of queries / state-sets / tiny relative moves, each `ok` |

## Capturing more

Run the hardware lane against a board (motor/heater power optional):

```sh
uv run pytest tests/hardware --port /dev/ttyACM0
```

To record a fresh trace, wrap the transport in `TracingTransport` with a file
sink (see `working/bringup.py`) and curate the relevant slice into a `.trace`
file here, then add assertions in `test_conformance.py`.
