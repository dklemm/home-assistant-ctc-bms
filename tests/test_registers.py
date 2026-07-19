"""Register map invariants (the generated file is data - test its shape)."""

import re

from custom_components.ctc_bms.registers import (
    HP_FIELDS,
    MAX_HEAT_PUMPS,
    MAX_ZONES,
    SYSTEM_REGISTERS,
    ZONE_FIELDS,
    device_registers,
    registers_for_hp,
    registers_for_zone,
)


def test_hp_array_addressing():
    by_name = {r.name: r for r in registers_for_hp(1)}
    assert by_name["HP1 TempIn"].number == 62027
    assert by_name["HP1 Status"].number == 62017
    # stride 1: HP10's TempIn is 62036
    assert {r.name: r for r in registers_for_hp(10)}["HP10 TempIn"].number == 62036
    # stride 2 for the 32-bit fields
    assert by_name["HP1 CompressorTime"].number == 62214
    hp2 = {r.name: r for r in registers_for_hp(2)}
    assert hp2["HP2 CompressorTime"].number == 62216
    assert hp2["HP2 CompressorTime"].count == 2


def test_zone_array_addressing():
    assert {r.name: r for r in registers_for_zone(1)}["Zone1 SetPoint"].number == 61509
    assert {r.name: r for r in registers_for_zone(4)}["Zone4 SetPoint"].number == 61512


def test_no_array_rows_leaked_into_system():
    # Regression: the manual leaves the Name column blank for some array rows;
    # if the generator's description matching regresses, they reappear in the
    # system list as N unrelated registers (this bug shipped twice).
    leaked = [
        r
        for r in SYSTEM_REGISTERS
        if re.search(r"(heat pump|compressor|hp|heating system)\s*\d+", r.desc, re.I)
    ]
    assert leaked == []


def test_device_split():
    devices = device_registers()
    assert set(devices) == (
        {"System"}
        | {f"HP{n}" for n in range(1, MAX_HEAT_PUMPS + 1)}
        | {f"Zone{n}" for n in range(1, MAX_ZONES + 1)}
    )
    assert len(devices["HP1"]) == len(HP_FIELDS) == 25
    assert len(devices["Zone1"]) == len(ZONE_FIELDS) == 20
    assert len(devices["System"]) == 224
