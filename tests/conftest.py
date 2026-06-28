"""Shared pytest config: the opt-in hardware-test gate.

The default suite is 100% hardware-free (FakeTransport / recorded-trace replay).
Tests marked ``@pytest.mark.hardware`` talk to a real Marlin controller and are
**skipped unless** a port is supplied:

    uv run pytest --port /dev/ttyACM0          # run everything incl. hardware
    uv run pytest --port /dev/ttyACM0 --baud 250000

With no ``--port`` (the CI default) the hardware tests are collected but skipped,
so they never gate a hardware-free run.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--port",
        action="store",
        default=None,
        help="Serial port of a real Marlin controller; enables @pytest.mark.hardware tests",
    )
    parser.addoption(
        "--baud", action="store", default="250000", help="Baud rate for hardware tests"
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "hardware: requires a real Marlin controller; runs only with --port"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--port"):
        return  # a port was supplied — let the hardware tests run
    skip = pytest.mark.skip(reason="hardware test — pass --port /dev/ttyACMx to run")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def port(request: pytest.FixtureRequest) -> str:
    value = request.config.getoption("--port")
    assert value is not None, "hardware tests require --port"  # gated by collection skip
    return str(value)


@pytest.fixture
def baud(request: pytest.FixtureRequest) -> int:
    return int(request.config.getoption("--baud") or 250000)
