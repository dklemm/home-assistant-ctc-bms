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
    61500: 1,          # DHW mode = Normal (RW enum -> select entity)
    61501: s16(500),   # DHW manual stop temp = 50.0 C (RW -> number entity)
    61509: s16(215),   # Zone1 room setpoint = 21.5 C (RW -> number entity)
    61521: 1,          # HP1 Blocked = 1 i.e. Allowed (RW bool -> switch, off)
    61542: 0,          # Zone1 heating mode = Auto (RW enum -> select)
    61554: s16(50),    # Zone1 night reduction = 5.0 C (RW -> number entity)
    61572: s16(900),   # HP1 RPSMax = 90.0 (RW -> number entity)
    61658: 1,          # Pool enable: RW, but read-only until the values are known
    61671: 1,          # HC1 heating program = Normal (RW enum -> select)
    62000: s16(-53),   # outside temp = -5.3 C
    62016: 1,          # DHW circulation pump running (R bool -> binary_sensor)
    62017: 3,          # HP1 status = compressor on
    # The EcoLogic M's DHW reality: of five documented tank temperatures only
    # the upper sensor is populated, and the others sit at 0 rather than at the
    # sentinel. 62002/62003/62275 are left out of this dict on purpose, so they
    # read 0 exactly as the hardware does.
    62276: s16(610),   # DHW upper = 61.0 C - the only real tank reading
    62027: s16(466),   # HP1 temp in = 46.6 C
    62186: 8,          # total operation LSB
    62187: 0,          # total operation MSB -> 8 h
    62181: 0,          # Solar mode off (R bool -> binary_sensor)
    62203: 55536,      # Zone1 current room temp: no sensor fitted
    62304: 1,          # RadiatorPump1 running (R bool -> binary_sensor)
    62313: 1,          # HotWaterValve diverted to DHW (R -> valve, open)
    62291: s16(132),   # HP1 primary system flow = 13.2 l/min
    62214: 12345,      # HP1 compressor time LSB
    62215: 0,          # HP1 compressor time MSB -> 12345 h
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_registers() -> dict[int, int]:
    """The register map behind mock_hub; mutate it before setting an entry up.

    A fixture rather than an import: without tests/__init__.py, `from
    tests.conftest import ...` gets a *second* copy of this module and the
    mutation lands on a dict nobody reads.
    """
    return FAKE_REGISTERS


@pytest.fixture
def mock_hub():
    """Patch CtcHub's I/O so setup runs against FAKE_REGISTERS, no sockets.

    Mirrors the real pump's behaviour: every polled address answers (absent
    hardware reads 0), so reads return a word for everything asked.
    """
    writes: list[tuple[int, int]] = []
    # Writes land in FAKE_REGISTERS so a read-back sees them; undo that
    # afterwards so tests can't leak values into each other.
    original = FAKE_REGISTERS.copy()

    async def fake_probe(self) -> None:
        return None

    async def fake_read(self, addrs: set[int]) -> dict[int, int]:
        return {a: FAKE_REGISTERS.get(a, 0) for a in addrs}

    async def fake_write(self, address: int, value: int) -> None:
        writes.append((address, value))
        FAKE_REGISTERS[address] = value

    with (
        patch.object(CtcHub, "async_probe", fake_probe),
        patch.object(CtcHub, "async_read_addresses", fake_read),
        patch.object(CtcHub, "async_write_register", fake_write),
    ):
        yield writes
    FAKE_REGISTERS.clear()
    FAKE_REGISTERS.update(original)
