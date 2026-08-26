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


def test_dhw_temperatures_treat_zero_as_no_reading():
    # Measured on an EcoLogic M with a 61 C tank: only 62276 was populated and
    # the rest sat at 0, so "Temperature" (62003) led the Hot Water device with
    # a headline 0.0 C.
    for number in (62002, 62003, 62275, 62276):
        assert override_for(number).zero_is_unknown is True


def test_zero_stays_a_real_reading_where_it_is_one():
    # The counter-examples that keep this from becoming a blanket rule: 0 C
    # outdoors is an ordinary morning, and 0% DHW capacity is an empty tank.
    for number in (62000, 62279):
        assert override_for(number).zero_is_unknown is False


def test_zero_is_unknown_is_confined_to_temperatures():
    # A percentage, a count or a status can all legitimately read 0. Only a
    # quantity that physically cannot sit at zero may claim this.
    units = {r.number: r.unit for r in SYSTEM_REGISTERS}
    for number, o in OVERRIDES.items():
        if o.zero_is_unknown:
            # 62002 is a DHW setpoint the generator left unitless; the rest are
            # degrees. Either way, never a %, an hour count or a status.
            assert units[number] in ("°C", ""), number


def test_overrides_reference_real_registers():
    numbers = {r.number for r in SYSTEM_REGISTERS}
    assert set(OVERRIDES) <= numbers
