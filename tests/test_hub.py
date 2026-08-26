"""Hub batching/bisection behaviour against modbus-connection's mock unit.

The mock reproduces the controller's defining quirk: `fail_read` makes every
block read *covering* that address raise, so one absent register takes the
whole block down - and it raises ModbusTimeoutError, which is the silence the
hub reads as "absent address".
"""

from __future__ import annotations

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusTimeoutError,
)

from custom_components.ctc_bms.const import PROBE_REGISTER
from custom_components.ctc_bms.hub import CtcConnectionError, CtcHub


def make_hub(unit, store: dict[int, int]) -> CtcHub:
    hub = CtcHub("127.0.0.1", 502, 1)
    hub._unit = unit
    unit.holding.update(store)
    return hub


async def test_block_read_groups_spans(mock_modbus_unit):
    # Two clusters far apart must not be read as one bisected mega-range.
    store = {a: a for a in range(61500, 61620)} | {
        a: a for a in range(62000, 62100)
    }
    hub = make_hub(mock_modbus_unit, store)
    # A read that crossed the gap would cover this and go silent.
    mock_modbus_unit.fail_read(61800, ModbusTimeoutError())
    wanted = {61500, 61510, 62000, 62050}
    result = await hub.async_read_addresses(wanted)
    assert result[61500] == 61500 and result[62050] == 62050
    # one block per cluster, nothing in the dead gap between them
    assert len(mock_modbus_unit.read_events) == 2


async def test_bisection_isolates_dead_address(mock_modbus_unit):
    store = {a: a for a in range(62000, 62100)}
    hub = make_hub(mock_modbus_unit, store)
    # One absent register inside the block: every read covering it is silent.
    mock_modbus_unit.fail_read(62042, ModbusTimeoutError())
    wanted = set(range(62000, 62080))
    result = await hub.async_read_addresses(wanted)
    assert 62042 not in result
    assert result[62041] == 62041 and result[62043] == 62043
    assert 62042 in hub.dead_addresses

    # Second poll: the dead address is excluded, so the remaining spans read
    # clean in exactly two blocks (one either side of the hole).
    mock_modbus_unit.read_events.clear()
    result = await hub.async_read_addresses(wanted)
    assert 62042 not in result and len(result) == 79
    assert len(mock_modbus_unit.read_events) == 2


async def test_refused_address_is_absent_not_a_failure(mock_modbus_unit):
    """Exception code 2 means the same as silence: bisect, don't fail the poll.

    The CTC goes quiet, but the simulator - and any politer firmware - answers
    an unserved address with ILLEGAL_DATA_ADDRESS.
    """
    hub = make_hub(mock_modbus_unit, {a: a for a in range(62000, 62100)})
    mock_modbus_unit.fail_read(62042, IllegalDataAddressError())
    result = await hub.async_read_addresses(set(range(62000, 62080)))
    assert 62042 not in result and hub.dead_addresses == {62042}
    assert result[62043] == 62043


async def test_dead_link_raises_after_one_probe(mock_modbus_unit):
    hub = make_hub(mock_modbus_unit, {})
    mock_modbus_unit.fail_requests(ModbusTimeoutError())  # nothing answers
    with pytest.raises(CtcConnectionError):
        await hub.async_read_addresses(set(range(62000, 62080)))
    # first block + one probe: no bisection storm of timeouts
    reads = mock_modbus_unit.read_events
    assert len(reads) == 2
    assert (reads[1].address, reads[1].count) == (PROBE_REGISTER, 1)


async def test_connection_error_is_not_an_absent_address(mock_modbus_unit):
    """A dropped link must not be bisected: it would cache live regs as dead."""
    hub = make_hub(mock_modbus_unit, {a: a for a in range(62000, 62100)})
    mock_modbus_unit.fail_requests(ModbusConnectionError("link down"))
    with pytest.raises(CtcConnectionError):
        await hub.async_read_addresses(set(range(62000, 62080)))
    assert hub.dead_addresses == set()
    assert len(mock_modbus_unit.read_events) == 1  # no probe, no bisection


async def test_write_uses_fc16(mock_modbus_unit):
    hub = make_hub(mock_modbus_unit, {61501: 450})
    writes = []
    mock_modbus_unit.on_write(writes.append)
    await hub.async_write_register(61501, 500)
    assert [(w.address, w.values, w.function_code) for w in writes] == [
        (61501, [500], 0x10)  # FC16, per the BMS manual
    ]
    assert mock_modbus_unit.holding[61501] == 500
