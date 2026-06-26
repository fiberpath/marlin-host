# marlin-host

A host-side Python library for the **Marlin** firmware serial protocol — the
PC/host end of the line-numbered, checksummed, `ok`-acknowledged G-code
conversation that hosts like OctoPrint and Pronterface speak to a printer or
CNC running Marlin. Neutral and standalone; not tied to any one application.

> **Status:** early scaffolding. Developed against the Marlin firmware source
> and official docs. Licensed Apache-2.0.

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
