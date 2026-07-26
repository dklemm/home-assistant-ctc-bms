"""Which device each system register lands on."""

import collections

from custom_components.ctc_bms.groups import SUBSYSTEMS, group_for
from custom_components.ctc_bms.registers import SYSTEM_REGISTERS


def buckets() -> dict[str, list]:
    out = collections.defaultdict(list)
    for reg in SYSTEM_REGISTERS:
        out[group_for(reg)].append(reg)
    return out


def test_grouping_is_total_and_disjoint():
    # The gate: a regenerated map that adds registers must not silently drop
    # them, and no register may be claimed by two devices.
    grouped = [r for regs in buckets().values() for r in regs]
    assert len(grouped) == len(SYSTEM_REGISTERS)
    assert {r.number for r in grouped} == {r.number for r in SYSTEM_REGISTERS}


def test_group_sizes():
    # Snapshot, so a regenerated map that reshuffles the buckets fails loudly.
    sizes = {k: len(v) for k, v in buckets().items()}
    assert sizes == {
        "System": 42,
        "AddHeat": 47,
        "Cooling": 32,
        "DHW": 25,
        "Solar": 18,
        "Pool": 8,
        "Ventilation": 8,
        "Zone1": 11,
        "Zone2": 11,
        "Zone3": 11,
        "Zone4": 11,
    }
    assert sum(sizes.values()) == 224


def test_only_curve_registers_go_to_zones():
    for key, regs in buckets().items():
        if not key.startswith("Zone"):
            continue
        n = key.removeprefix("Zone")
        for reg in regs:
            assert reg.name.startswith((f"hc{n}_", f"heating_program_hc{n}"))


def test_known_registers():
    by_number = {r.number: r for r in SYSTEM_REGISTERS}
    expected = {
        62000: "System",  # sOutsideTemp
        62003: "DHW",  # sDHWTemp
        62313: "DHW",  # hotwatervalve
        61591: "DHW",  # elhDHWMaxPower - DHW wins over the elh add-heat rule
        62179: "Pool",  # poolTemp
        62182: "Solar",  # sunTempOut
        62250: "AddHeat",  # exbUpperTemp
        62362: "AddHeat",  # hbt_top_temp - via _OVERRIDES
        62209: "AddHeat",  # woodBoilerTemp
        62289: "Cooling",  # cooling_tank_temp
        62280: "Ventilation",  # sFanExhaustPct
        61675: "Zone1",  # hc1 heating curve point 1
        61714: "Zone4",  # hc4 heating curve point 5
        62304: "System",  # radiatorpump1
    }
    assert {n: group_for(by_number[n]) for n in expected} == expected


def test_subsystem_keys_are_all_reachable():
    assert set(SUBSYSTEMS) <= set(buckets())
