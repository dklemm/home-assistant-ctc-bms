"""Entity names: coverage, uniqueness and house style."""

import pytest

from custom_components.ctc_bms import const
from custom_components.ctc_bms.entity import register_entity_name
from custom_components.ctc_bms.groups import group_for
from custom_components.ctc_bms.names import (
    NAME_HP_FIELDS,
    NAME_SYSTEM,
    NAME_ZONE_FIELDS,
    name_for,
)
from custom_components.ctc_bms.registers import (
    HP_FIELDS,
    SYSTEM_REGISTERS,
    ZONE_FIELDS,
    registers_for_hp,
    registers_for_zone,
)


def _field(reg) -> str:
    return reg.name.split(" ", 1)[1] if " " in reg.name else reg.name


def _ships(reg) -> bool:
    """Whether this register becomes an entity, per coordinator.platform_for().

    Mirrored here rather than driven through a real coordinator so the naming
    audit stays a socket-free unit test; setpoints are assumed on, which is the
    widest the entity set ever gets.
    """
    if reg.access == "R" or reg.number in const.READ_ONLY_RW:
        return True
    if reg.device == "System":
        return (
            reg.number in const.SELECT_SYSTEM
            or reg.number in const.SWITCH_SYSTEM
            or reg.number in const.SETPOINT_SYSTEM
        )
    tables = (
        (
            const.SELECT_HP_FIELDS,
            const.SWITCH_HP_FIELDS,
            const.SETPOINT_HP_FIELDS,
        )
        if reg.device.startswith("HP")
        else (
            const.SELECT_ZONE_FIELDS,
            const.SWITCH_ZONE_FIELDS,
            const.SETPOINT_ZONE_FIELDS,
        )
    )
    return any(_field(reg) in t for t in tables)


def _shipped() -> list[tuple[str, object]]:
    """(device key, register) for every entity the integration creates.

    Heat pump 1 and zone 1 stand in for all ten/four: the arrays are named by
    field, so member 2..N can't differ.
    """
    rows = [(group_for(reg), reg) for reg in SYSTEM_REGISTERS]
    rows += [("HP1", reg) for reg in registers_for_hp(1)]
    rows += [("Zone1", reg) for reg in registers_for_zone(1)]
    return [(key, reg) for key, reg in rows if _ships(reg)]


def test_every_shipped_register_has_a_curated_name():
    """No entity falls back to a name derived from the manual's Name column."""
    missing = [
        (reg.number, reg.name) for _, reg in _shipped() if name_for(reg) is None
    ]
    assert not missing


def test_names_are_unique_within_a_device():
    seen: dict[tuple[str, str], int] = {}
    for key, reg in _shipped():
        name = register_entity_name(key, reg)
        clash = seen.setdefault((key, name), reg.number)
        assert clash == reg.number, f"{key}: {name} on {clash} and {reg.number}"


# Mixed-case words that are correct as written; anything else after the first
# word must be lower case (sentence case) or an all-caps acronym.
_MIXED_CASE_OK = {"kW", "MWh"}


_ALL_NAMES = sorted(
    {*NAME_SYSTEM.values(), *NAME_HP_FIELDS.values(), *NAME_ZONE_FIELDS.values()}
)


@pytest.mark.parametrize("name", _ALL_NAMES)
def test_names_are_sentence_case(name: str):
    assert name == name.strip()
    assert name[0].isupper()
    for word in name.split()[1:]:
        bare = word.strip("(),/")
        if not bare or not bare[0].isalpha() or bare in _MIXED_CASE_OK:
            continue
        assert bare.islower() or bare.isupper(), f"{name!r}: {word!r}"


def test_name_tables_reference_real_registers_and_fields():
    assert set(NAME_SYSTEM) <= {r.number for r in SYSTEM_REGISTERS}
    assert set(NAME_HP_FIELDS) <= {f.field for f in HP_FIELDS}
    assert set(NAME_ZONE_FIELDS) <= {f.field for f in ZONE_FIELDS}


def test_the_names_that_prompted_the_table():
    """Regressions for the specific manual-shorthand names users hit first."""
    hp1 = {
        reg.number: register_entity_name("HP1", reg)
        for reg in registers_for_hp(1)
    }
    assert hp1[62193] == "RPS"  # was "Curr RPS"
    assert hp1[61572] == "Max RPS"  # was "RPS Max"
    assert hp1[62047] == "Discharge gas"  # was "Discharge Gas"
    zone1 = {
        reg.number: register_entity_name("Zone1", reg)
        for reg in registers_for_zone(1)
    }
    assert zone1[62203] == "Room temperature"  # was "Temp Curr"
    system = {reg.number: reg for reg in SYSTEM_REGISTERS}
    # Was "Current", though the manual calls it the maximum.
    assert register_entity_name("System", system[62170]) == "Maximum current"
    # Was "Sgmode 0 none normal 1 block 2 low price" - the legend, slugged.
    assert register_entity_name("System", system[62301]) == "Smart grid mode"
    # Was "Hotwatervalve": real camelCase, flattened by the slug fallback.
    assert register_entity_name("DHW", system[62313]) == "Diverter valve"


def test_derived_fallback_is_sentence_case():
    """An unnamed register still gets a readable name, not the raw shorthand."""
    from custom_components.ctc_bms.registers import Reg

    reg = Reg(99999, "sSomeNewThing", "invented", "S16", 1.0, "", "R", "System")
    assert name_for(reg) is None
    assert register_entity_name("System", reg) == "Some new thing"
    hp = Reg(99998, "HP1 SomeRPSThing", "invented", "S16", 1.0, "", "R", "HP1")
    assert register_entity_name("HP1", hp) == "Some RPS thing"
