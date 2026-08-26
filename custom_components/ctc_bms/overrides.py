"""Hand-written per-register presentation overrides.

Strictly things the generated map gets *wrong*, replaced by register number:

- **Units.** The generator infers units from the manual's descriptions, which
  only give a scale factor, so some come out wrong or blank - the supply
  currents are unitless when they're Amps.
- **Which entities to hide.** Some registers are real but rarely wanted: the
  3-phase supply currents (most installs don't wire all three, so they read 0)
  and the immersion-heater power (absent or idle on many units). These are
  created but start disabled, so they're one click away without cluttering.
- **Where 0 means "no reading".** The map says a register is a temperature; it
  does not say the controller leaves the ones it isn't computing at 0 rather
  than at the -9999/-10000 sentinel. See `zero_is_unknown` below.

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
    # True: a raw 0 reads as unknown, like the -9999/-10000 sentinel. Only for
    # registers where 0 is not a value the quantity can actually take - see the
    # note above OVERRIDES. Never a blanket rule: 0 degrees outdoors is a
    # perfectly ordinary morning.
    zero_is_unknown: bool = False


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
    #
    # DHW temperatures the controller leaves at 0 when it isn't computing them.
    # The map documents five DHW temperature registers; a given install
    # populates the ones its tank arrangement has and parks the rest at 0 - not
    # at the -9999/-10000 "no sensor" sentinel, which would have been honest.
    # Measured on an EcoLogic M (system type 5, DHW in Comfort, stop temp 60)
    # with a tank at 61 C: only 62276 carried it.
    #
    #     62002 sSetPDHW       0      62276 sDHWUpperTemp   610  <- the tank
    #     62003 sDHWTemp       0
    #     62275 sDHWLowerTemp  0
    #
    # 62003 is the one named plainly "Temperature", so the CTC Hot Water device
    # led with a headline 0.0 C while the water was scalding. Unknown is the
    # truthful state: the register answered, it just holds no reading.
    #
    # Safe because none of these can legitimately read 0. A stored-water
    # temperature of exactly 0.0 C is a frozen tank, and 62002 is a *setpoint* -
    # the controller has no 0 C hot-water setting to hold. Contrast 62000
    # outdoor temperature, where 0 is ordinary, and 62279 sDHWCapacity, where
    # 0% is a real (empty) tank: both deliberately left alone.
    62002: Override(zero_is_unknown=True),  # sSetPDHW
    62003: Override(zero_is_unknown=True),  # sDHWTemp
    62275: Override(zero_is_unknown=True),  # sDHWLowerTemp
    62276: Override(zero_is_unknown=True),  # sDHWUpperTemp
}


def override_for(number: int) -> Override:
    """The override for a register, or an all-defaults one."""
    return OVERRIDES.get(number, _DEFAULT)
