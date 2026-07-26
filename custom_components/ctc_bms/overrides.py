"""Hand-written per-register presentation overrides.

Strictly things the generated map gets *wrong*, replaced by register number:

- **Units.** The generator infers units from the manual's descriptions, which
  only give a scale factor, so some come out wrong or blank - the supply
  currents are unitless when they're Amps.
- **Which entities to hide.** Some registers are real but rarely wanted: the
  3-phase supply currents (most installs don't wire all three, so they read 0)
  and the immersion-heater power (absent or idle on many units). These are
  created but start disabled, so they're one click away without cluttering.

Entity names are NOT here, though they were once: the manual gives no display
names at all, so naming isn't overriding anything. See names.py.

This file survives regeneration of the map. Add a register number here when you
find one whose unit is wrong or that should ship switched off.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Override:
    # Replaces reg.unit for HA unit / device-class mapping (see sensor._UNIT_MAP).
    unit: str | None = None
    # Extra multiplier on the decoded value, for a unit that isn't the map's
    # native scale - e.g. factor=1000 to show a kW register in W.
    factor: float = 1.0
    # False: the entity is created but disabled in the registry (one click to
    # enable), rather than absent.
    enabled_default: bool = True


_DEFAULT = Override()

OVERRIDES: dict[int, Override] = {
    # 3-phase supply current - unitless in the generated map, and most installs
    # don't wire all three phases (they read 0), so ship them off. Amps.
    62170: Override(unit="A", enabled_default=False),  # sCurrent (maximum)
    62171: Override(unit="A", enabled_default=False),  # s1Current L1
    62172: Override(unit="A", enabled_default=False),  # s2Current L2
    62173: Override(unit="A", enabled_default=False),  # s3Current L3
    # Immersion-heater power - kW is already a valid power unit, but the heater
    # is absent or idle on many installs (reads 0), so ship them off.
    62168: Override(enabled_default=False),  # sPowerConsumption
    62169: Override(enabled_default=False),  # sPowerConsumptionHS
}


def override_for(number: int) -> Override:
    """The override for a register, or an all-defaults one."""
    return OVERRIDES.get(number, _DEFAULT)
