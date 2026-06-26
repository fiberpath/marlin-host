"""Tests for host->device line framing (line numbering + checksum).

Checksum vectors are the canonical RepRap/Marlin examples `N3 T0*57` and
`N5 G28*22`, and match Marlin's algorithm in `src/gcode/queue.cpp`
(`checksum ^= command[i]` over every byte before the `*`).
"""

from __future__ import annotations

import pytest

from marlin_host.framing import checksum, frame, reset_line_number


def test_checksum_known_vectors() -> None:
    assert checksum("N3 T0") == 57
    assert checksum("N5 G28") == 22


def test_checksum_empty_is_zero() -> None:
    assert checksum("") == 0


def test_checksum_is_a_byte() -> None:
    for line in ("N3 T0", "N5 G28", "N127 G1 X10.5 A360 B0.25"):
        assert 0 <= checksum(line) <= 255


def test_frame_matches_canonical_examples() -> None:
    assert frame(3, "T0") == "N3 T0*57"
    assert frame(5, "G28") == "N5 G28*22"


def test_frame_checksum_covers_the_line_number() -> None:
    # The checksum is over `N<n> <gcode>`, so changing the line number changes it.
    assert frame(3, "T0") != frame(4, "T0").replace("N4", "N3", 1)


def test_reset_line_number_uses_m110() -> None:
    cmd = reset_line_number(0)
    assert cmd == frame(0, "M110 N0")
    assert cmd.startswith("N0 M110 N0*")


def test_frame_rejects_embedded_newline() -> None:
    with pytest.raises(ValueError):
        frame(1, "G1 X10\nG1 X20")
