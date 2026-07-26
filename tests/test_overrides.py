"""Per-register presentation overrides."""

from custom_components.ctc_bms.overrides import (
    OVERRIDES,
    Override,
    override_for,
)
from custom_components.ctc_bms.registers import SYSTEM_REGISTERS


def test_default_is_all_defaults():
    d = override_for(99999)
    assert d == Override()
    assert d.unit is None and d.factor == 1.0 and d.enabled_default is True


def test_three_phase_currents_are_amps_and_disabled():
    for number in (62170, 62171, 62172, 62173):
        o = override_for(number)
        assert o.unit == "A"
        assert o.enabled_default is False


def test_immersion_power_is_disabled_but_keeps_its_unit():
    for number in (62168, 62169):
        o = override_for(number)
        assert o.enabled_default is False
        assert o.unit is None  # stays the register's kW


def test_factor_is_carried_for_unit_conversions():
    # Not used by any real register yet (power stays kW), but the mechanism is
    # here for a register that needs, say, kW shown as W.
    assert Override(unit="W", factor=1000).factor == 1000


def test_overrides_reference_real_registers():
    numbers = {r.number for r in SYSTEM_REGISTERS}
    assert set(OVERRIDES) <= numbers
