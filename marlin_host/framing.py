"""Host->device line framing: line numbering + checksum (Marlin's robust protocol).

:func:`frame` produces ``N<line_number> <gcode>*<checksum>``. The checksum is the
XOR of every byte before the ``*``, matching Marlin's ``src/gcode/queue.cpp``
(``checksum ^= command[i]``). Pure stdlib; no transport involved.
"""

from __future__ import annotations

__all__ = ["checksum", "frame", "reset_line_number"]


def checksum(line: str) -> int:
    """Return the XOR checksum (0-255) of ``line`` — the bytes before the ``*``."""
    value = 0
    for byte in line.encode("ascii"):
        value ^= byte
    return value


def frame(line_number: int, gcode: str) -> str:
    """Frame a command as ``N<line_number> <gcode>*<checksum>``."""
    if "\n" in gcode or "\r" in gcode:
        raise ValueError("gcode must be a single line (no newline)")
    content = f"N{line_number} {gcode}"
    return f"{content}*{checksum(content)}"


def reset_line_number(line_number: int = 0) -> str:
    """The M110 command that (re)sets Marlin's expected next line number."""
    return frame(line_number, f"M110 N{line_number}")
