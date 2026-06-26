#!/usr/bin/env python3
"""Fetch the pinned Marlin source files that codegen needs into ``vendor/marlin/``.

Downloads only the specific files the generator parses, at a **pinned commit**,
so codegen is deterministic and CI-friendly (no full-repo clone). It reads
nothing from the local ``_reference/`` audit copy — this is the reproducible path.

Update the pin by bumping ``MARLIN_REF``/``MARLIN_SHA`` (resolve a tag with
``git ls-remote https://github.com/MarlinFirmware/Marlin.git <tag>``).
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

# Pinned Marlin reference (latest stable line). Bump deliberately.
MARLIN_REF = "latest-2.1.2.x"
MARLIN_SHA = "1cd56c4ccd483045eb5a92c99e3ad3b5ab1bea6d"

_RAW = "https://raw.githubusercontent.com/MarlinFirmware/Marlin"

# Source files the constants generator parses (paths within the Marlin repo).
FILES = [
    "Marlin/src/core/language.h",
]

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "marlin"


def main() -> None:
    for rel in FILES:
        url = f"{_RAW}/{MARLIN_SHA}/{rel}"
        dest = _VENDOR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as resp:  # noqa: S310 - fixed https URL
            dest.write_bytes(resp.read())
        print(f"fetched {rel}")
    (_VENDOR / "MARLIN_REF").write_text(f"{MARLIN_REF}\n{MARLIN_SHA}\n")
    print(f"Marlin {MARLIN_REF} @ {MARLIN_SHA[:8]} -> {_VENDOR}")


if __name__ == "__main__":
    main()
