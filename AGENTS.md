# AGENTS.md

Instructions for AI coding agents working in this repository.

## Project

`marlin-host` is a host-side Python library implementing the **Marlin** firmware
serial protocol — the PC/host end of the line-numbered, checksummed,
`ok`-acknowledged G-code conversation (the role OctoPrint/Pronterface play). It
is neutral and standalone (no application coupling). The protocol is modelled
against the Marlin 2.1.x firmware **source** as the binding contract.

## Stack

- **Language** — Python `>=3.11`, package manager **uv**.
- **Tooling** — `ruff` (format + lint), `pyright` (type check), `pytest` (tests).
- **No runtime dependencies** — the protocol layer is pure stdlib.

## Layout

```
marlin_host/      # library code (flat package)
tests/            # tests, mirroring module names (test_<module>.py)
working/          # gitignored: scratch docs/research we generate
_reference/       # gitignored: pulled Marlin source/docs to build against
planning/         # gitignored: planning notes
```

## Commands

```sh
just setup        # uv sync
just fmt          # ruff format + ruff check --fix
just check        # fmt-check + lint + typecheck (CI-equivalent)
just test         # pytest
```

## Conventions

- Conventional Commits (`type(scope): subject`, imperative, ≤72 chars).
- Run `just check && just test` before committing; never bypass hooks.
- Protocol facts must be grounded in the Marlin source — cite where non-obvious.
- Tests mirror source structure (`tests/test_<module>.py`).
- Keep the library dependency-free unless a transport genuinely needs one
  (e.g. `pyserial`, behind an optional extra).

## Constraints

- Do not commit anything under `working/`, `_reference/`, or `planning/`.
- Do not add runtime dependencies without justification.
- Do not commit secrets or credentials.
