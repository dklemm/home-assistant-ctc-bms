"""Which HA device each system register belongs to.

The BMS manual lists every non-array register in one flat table, but that table
covers several distinct subsystems - hot water, solar, pool, cooling,
ventilation, additional heat - plus the per-circuit heating curves, which belong
to the heating-system (Zone) devices the integration already builds. Splitting
them here turns ~180 entities on one device page into a tree that mirrors the
plant.

This is a presentation concern, not a protocol fact, so it lives here rather
than in the generated registers.py - regenerating the map from the PDF must not
clobber it. tests/test_registers.py asserts the grouping stays total and
disjoint, so a regenerated map that adds registers fails loudly instead of
silently dropping them.
"""

from __future__ import annotations

import re

from .registers import Reg

# Subsystem key -> device name. Keys are also used to build device identifiers,
# so don't rename them without a migration.
SUBSYSTEMS: dict[str, str] = {
    "DHW": "CTC Hot Water",
    "Solar": "CTC Solar",
    "Pool": "CTC Pool",
    "Cooling": "CTC Cooling",
    "Ventilation": "CTC Ventilation",
    "AddHeat": "CTC Additional Heat",
}

# Per-circuit settings that the manual happens to list in the flat table.
_ZONE_RE = re.compile(r"^(?:hc|heating_program_hc)(\d)")

# Ordered: first match wins. Order is load-bearing - elhDHWMaxPower must reach
# the DHW rule before the "elh" additional-heat rule, and cooling's
# primary_flow_* must be claimed before anything generic.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Ventilation", ("svent", "sfanexhaust", "shighestco2", "shighestrh")),
    ("Pool", ("pool", "spool")),
    ("Solar", ("sun", "solar", "ssuntank")),
    (
        "Cooling",
        (
            "cool",
            "active_cooling",
            "room_temp_cooling",
            "current_room_temp_cooling",
            "primary_flow",
            "delay_cooling",
        ),
    ),
    ("DHW", ("hotwatervalve",)),
    (
        "AddHeat",
        (
            "exb",
            "ex1",
            "ex2",
            "ex3",
            "ehs",
            "elh",
            "wood",
            "extboiler",
            "el1_",
            "el2_",
            "el3_",
            "elheater",
            "evk_",
        ),
    ),
]

# Exact-name matches for registers the prefix rules can't reach.
_EXACT: dict[str, str] = {
    "e1": "AddHeat",
    "e4": "AddHeat",
}

# Judgement calls the rules get wrong, keyed by register number.
_OVERRIDES: dict[int, str] = {
    62362: "AddHeat",  # hbt_top_temp - heating buffer tank
}

# Deliberately NOT grouped: the differential thermostat (dth*,
# sDiffThermostat*) drives both solar and wood-boiler charging depending on the
# install, so it stays on the controller rather than being guessed onto one.


def group_for(reg: Reg) -> str:
    """Device key for a system register: 'System', a SUBSYSTEMS key, or ZoneN.

    A ZoneN result is returned whether or not that zone exists; the coordinator
    drops registers whose group isn't in its active device set, which is what
    keeps absent zones out of the poll.
    """
    if reg.number in _OVERRIDES:
        return _OVERRIDES[reg.number]
    name = reg.name.lower()
    if zone := _ZONE_RE.match(name):
        return f"Zone{zone.group(1)}"
    if name in _EXACT:
        return _EXACT[name]
    if "dhw" in name:
        return "DHW"
    for key, prefixes in _RULES:
        if name.startswith(prefixes):
            return key
    return "System"
