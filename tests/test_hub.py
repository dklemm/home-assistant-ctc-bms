"""Hub batching/bisection behaviour against a mock Modbus client.

The mock reproduces the controller's defining quirk: a block read where ANY
address is absent returns silence (an error), not a partial answer.
"""

from __future__ import annotations

import pytest

from custom_components.ctc_bms.const import PROBE_REGISTER
from custom_components.ctc_bms.hub import CtcConnectionError, CtcHub


class _Response:
    def __init__(self, registers):
        self.registers = registers

    def isError(self):
        return False


class _ErrorResponse:
    def isError(self):
        return True


class FakeClient:
    def __init__(self, store: dict[int, int]):
        self.store = store
        self.reads: list[tuple[int, int]] = []
        self.writes: list[tuple[int, list[int]]] = []

    async def connect(self):
        return True

    def close(self):
        pass

    async def read_holding_registers(self, address, count=1, device_id=1):
        self.reads.append((address, count))
        if all(a in self.store for a in range(address, address + count)):
            return _Response(
                [self.store[a] for a in range(address, address + count)]
            )
        return _ErrorResponse()

    async def write_registers(self, address, values, device_id=1):
        self.writes.append((address, list(values)))
        for i, v in enumerate(values):
            self.store[address + i] = v
        return _Response([])


def make_hub(store: dict[int, int]) -> tuple[CtcHub, FakeClient]:
    hub = CtcHub("127.0.0.1", 502, 1)
    client = FakeClient(store)
    hub._client = client
    return hub, client


async def test_block_read_groups_spans():
    # Two clusters far apart must not be read as one bisected mega-range.
    store = {a: a for a in range(61500, 61620)} | {
        a: a for a in range(62000, 62100)
    }
    hub, client = make_hub(store)
    wanted = {61500, 61510, 62000, 62050}
    result = await hub.async_read_addresses(wanted)
    assert result[61500] == 61500 and result[62050] == 62050
    # one block per cluster, nothing in the dead gap between them
    assert len(client.reads) == 2


async def test_bisection_isolates_dead_address():
    store = {a: a for a in range(62000, 62100)}
    del store[62042]  # one absent register inside the block
    hub, client = make_hub(store)
    store[PROBE_REGISTER] = 1  # probe register lives
    wanted = set(range(62000, 62080))
    result = await hub.async_read_addresses(wanted)
    assert 62042 not in result
    assert result[62041] == 62041 and result[62043] == 62043
    assert 62042 in hub.dead_addresses

    # Second poll: the dead address is excluded, so the remaining spans read
    # clean in exactly two blocks (one either side of the hole).
    client.reads.clear()
    result = await hub.async_read_addresses(wanted)
    assert 62042 not in result and len(result) == 79
    assert len(client.reads) == 2


async def test_dead_link_raises_after_one_probe():
    hub, client = make_hub({})  # nothing answers at all
    with pytest.raises(CtcConnectionError):
        await hub.async_read_addresses(set(range(62000, 62080)))
    # first block + one probe: no bisection storm of timeouts
    assert len(client.reads) == 2


async def test_write_uses_fc16(monkeypatch):
    store = {61501: 450}
    hub, client = make_hub(store)
    await hub.async_write_register(61501, 500)
    assert client.writes == [(61501, [500])]  # write_registers = FC16
    assert store[61501] == 500
