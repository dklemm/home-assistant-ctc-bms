"""Shared fixtures for the CTC BMS tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.ctc_bms.hub import CtcHub


def s16(v: int) -> int:
    return v & 0xFFFF


# A minimal live pump: one heat pump running, one zone, a negative temperature,
# a "no sensor" sentinel and a 32-bit counter - the decode paths that matter.
FAKE_REGISTERS: dict[int, int] = {
    61501: s16(500),   # DHW manual stop temp = 50.0 C (RW -> number entity)
    61509: s16(215),   # Zone1 room setpoint = 21.5 C (RW -> number entity)
    61554: s16(50),    # Zone1 night reduction = 5.0 C (RW -> number entity)
    61572: s16(900),   # HP1 RPSMax = 90.0 (RW -> number entity)
    62000: s16(-53),   # outside temp = -5.3 C
    62017: 3,          # HP1 status = compressor on
    62027: s16(466),   # HP1 temp in = 46.6 C
    62186: 8,          # total operation LSB
    62187: 0,          # total operation MSB -> 8 h
    62203: 55536,      # Zone1 current room temp: no sensor fitted
    62214: 12345,      # HP1 compressor time LSB
    62215: 0,          # HP1 compressor time MSB -> 12345 h
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def mock_hub():
    """Patch CtcHub's I/O so setup runs against FAKE_REGISTERS, no sockets.

    Mirrors the real pump's behaviour: every polled address answers (absent
    hardware reads 0), so reads return a word for everything asked.
    """
    writes: list[tuple[int, int]] = []

    async def fake_connect(self) -> None:
        return None

    async def fake_probe(self) -> None:
        return None

    async def fake_read(self, addrs: set[int]) -> dict[int, int]:
        return {a: FAKE_REGISTERS.get(a, 0) for a in addrs}

    async def fake_write(self, address: int, value: int) -> None:
        writes.append((address, value))
        FAKE_REGISTERS[address] = value

    with (
        patch.object(CtcHub, "async_connect", fake_connect),
        patch.object(CtcHub, "async_probe", fake_probe),
        patch.object(CtcHub, "async_read_addresses", fake_read),
        patch.object(CtcHub, "async_write_register", fake_write),
    ):
        yield writes
