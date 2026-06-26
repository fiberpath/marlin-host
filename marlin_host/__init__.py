"""marlin-host: a host-side implementation of the Marlin serial protocol."""

from __future__ import annotations

from .framing import checksum, frame, reset_line_number
from .host import HaltError, HostError, MarlinHost, ProtocolError
from .protocol import MarlinResponse, MarlinResponseKind, parse_response
from .transport import FakeTransport, Transport

__all__ = [
    "MarlinHost",
    "HostError",
    "HaltError",
    "ProtocolError",
    "Transport",
    "FakeTransport",
    "parse_response",
    "MarlinResponse",
    "MarlinResponseKind",
    "frame",
    "checksum",
    "reset_line_number",
]

__version__ = "0.1.0"
