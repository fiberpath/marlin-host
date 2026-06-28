"""marlin-host: a host-side implementation of the Marlin serial protocol."""

from __future__ import annotations

from .framing import checksum, frame, reset_line_number
from .host import (
    Capabilities,
    HaltError,
    HostError,
    MarlinHost,
    Profile,
    ProtocolError,
    StreamProgress,
)
from .protocol import MarlinResponse, MarlinResponseKind, parse_response
from .transport import (
    FakeTransport,
    PortInfo,
    SerialTransport,
    TracingTransport,
    Transport,
    list_ports,
)

__all__ = [
    "MarlinHost",
    "HostError",
    "HaltError",
    "ProtocolError",
    "StreamProgress",
    "Profile",
    "Capabilities",
    "Transport",
    "FakeTransport",
    "SerialTransport",
    "TracingTransport",
    "PortInfo",
    "list_ports",
    "parse_response",
    "MarlinResponse",
    "MarlinResponseKind",
    "frame",
    "checksum",
    "reset_line_number",
]

__version__ = "0.1.0"
