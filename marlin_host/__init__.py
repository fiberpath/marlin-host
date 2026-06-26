"""marlin-host: a host-side implementation of the Marlin serial protocol."""

from __future__ import annotations

from .protocol import MarlinResponse, MarlinResponseKind, parse_response

__all__ = ["MarlinResponse", "MarlinResponseKind", "parse_response"]

__version__ = "0.1.0"
