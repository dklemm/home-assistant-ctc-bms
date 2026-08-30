"""The 1000-range control registers: the table, the hold, and the entities."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import entity_registry as er

from custom_components.ctc_bms.const import (
    CONF_CONTROLS,
    CONF_DEVICE_ID,
    CONF_HEAT_PUMPS,
    CONF_MODEL,
    CONF_SETPOINTS,
    CONF_SMARTGRID_A,
    CONF_SMARTGRID_B,
    CONF_SUBSYSTEMS,
    CONF_ZONES,
    DOMAIN,
)
from custom_components.ctc_bms.controls import (
    CONTROLS,
    NOT_CONTROLLED,
    VDI_REGISTER,
)
from custom_components.ctc_bms.hold import EXPIRY, REFRESH
from custom_components.ctc_bms.hub import CtcConnectionError
from custom_components.ctc_bms.registers import (
    SYSTEM_REGISTERS,
    all_registers,
)

CONTROL_RANGE = range(1000, 1101)


async def setup_entry(hass, **options) -> MockConfigEntry:
    """An entry with the controls on, plus whatever else the test needs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1.2.3.4:502:1",
        data={
            CONF_HOST: "1.2.3.4",
            CONF_PORT: 502,
            CONF_DEVICE_ID: 1,
            CONF_HEAT_PUMPS: [1],
            CONF_ZONES: [1],
            CONF_CONTROLS: True,
            **options,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_id(hass, entry, platform: str, key) -> str:
    registry = er.async_get(hass)
    eid = registry.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert eid, f"no {platform} entity for control {key}"
    return eid


async def enable(hass, entry, entity: str) -> None:
    """Turn on an entity that ships disabled, and reload so it exists."""
    er.async_get(hass).async_update_entity(entity, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def advance(hass, freezer, seconds: float) -> None:
    """Move the clock, so the refresh timer fires and the hold ages with it."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_every_control_is_a_control_register():
    numbers = [c.number for c in CONTROLS]
    assert len(numbers) == len(set(numbers))
    assert all(n in CONTROL_RANGE for n in numbers)


def test_no_control_collides_with_the_register_map():
    """The unique_id is the register number, so an overlap would make one
    entity shadow another."""
    mapped = {r.number for r in all_registers(1, 1)} | {
        r.number for r in SYSTEM_REGISTERS
    }
    assert not mapped & {c.number for c in CONTROLS}


def test_inferred_scales_match_their_sibling():
    """The manual gives no factor for any 1000-range register, so every numeric
    control borrows one from the stored parameter it shadows. If a regenerated
    map ever changes a sibling's factor, this fails loudly instead of silently
    mis-scaling a command to a live heating system."""
    by_number = {r.number: r for r in all_registers(1, 1)}
    borrowed = [c for c in CONTROLS if c.scale_from is not None]
    assert borrowed, "the inference is the point; don't delete the last one"
    for control in borrowed:
        sibling = by_number[control.scale_from]
        assert control.scale == sibling.scale, (
            f"control {control.number} borrows its scale from "
            f"{control.scale_from} ({sibling.name}), which is now "
            f"{sibling.scale}, not {control.scale}"
        )


def test_selects_carry_a_complete_legend():
    """Only registers whose full value set the manual spells out may be a
    select - the controller accepts undocumented values silently."""
    for control in CONTROLS:
        if control.kind != "select":
            continue
        assert control.options
        # The write path reverses this map.
        assert len(set(control.options.values())) == len(control.options)
        assert NOT_CONTROLLED not in control.options.values()


def test_zero_releases_every_numeric_control():
    """0 is how the UI releases a number, so it has to be reachable - and it is
    never a value the controller can hold, or (for the curve offsets) it means
    exactly what released means."""
    for control in CONTROLS:
        if control.kind != "number":
            continue
        low, high, _step = control.limits
        assert low <= 0 <= high, control.number


# ---------------------------------------------------------------------------
# Which controls exist
# ---------------------------------------------------------------------------


async def test_controls_are_off_by_default(hass, mock_hub):
    """No entry predates the controls step, so nothing is taken away by
    defaulting it off - unlike CONF_SETPOINTS, whose fallback stays True."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1.2.3.4:502:1",
        data={CONF_HOST: "1.2.3.4", CONF_PORT: 502, CONF_DEVICE_ID: 1},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert not [
        e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id.rsplit("_", 1)[-1].isdigit()
        and int(e.unique_id.rsplit("_", 1)[-1]) in CONTROL_RANGE
    ]


async def test_controls_do_not_need_the_writable_entities(hass, mock_hub):
    """Two gates, two questions: a control costs no write cycles, so turning
    the stored-parameter entities off must not take it away."""
    entry = await setup_entry(hass, **{CONF_SETPOINTS: False})
    assert hass.states.get(entity_id(hass, entry, "select", 1007))
    registry = er.async_get(hass)
    assert not registry.async_get_entity_id(
        "select", DOMAIN, f"{entry.entry_id}_61500"
    )


async def test_controls_follow_the_zone_and_subsystem_lists(hass, mock_hub):
    """No detection of its own: a control exists when the hardware it drives is
    already configured."""
    entry = await setup_entry(
        hass,
        **{CONF_ZONES: [1, 2], CONF_SUBSYSTEMS: ["DHW"]},
    )
    registry = er.async_get(hass)

    def exists(number: int) -> bool:
        return any(
            registry.async_get_entity_id(p, DOMAIN, f"{entry.entry_id}_{number}")
            for p in ("number", "select", "switch")
        )

    assert exists(1010) and exists(1011)  # zones 1 and 2
    assert not exists(1012) and not exists(1013)  # zones 3 and 4
    assert exists(1033)  # DHW tank setpoint
    assert not exists(1020)  # pool, not ticked
    assert exists(1002)  # System, always


async def test_ecologic_s_controls_need_that_model(hass, mock_hub):
    """1000/1001 are the only model-gated controls: that controller is for
    customers running their own logic, so the BMS starts the compressor."""
    entry = await setup_entry(hass, **{CONF_MODEL: "ecologic_m"})
    registry = er.async_get(hass)
    assert not registry.async_get_entity_id(
        "number", DOMAIN, f"{entry.entry_id}_1001"
    )

    entry_s = await setup_entry(hass, **{CONF_MODEL: "ecologic_s"})
    assert registry.async_get_entity_id(
        "number", DOMAIN, f"{entry_s.entry_id}_1001"
    )


async def test_controls_are_never_polled(hass, mock_hub):
    """Write-only, so a read of one is silence - which the hub cannot tell from
    a dead link. Polling them would buy a bisection of timeouts on the first
    poll and a permanently polluted dead-address cache, for nothing."""
    entry = await setup_entry(hass)
    wanted = entry.runtime_data._wanted
    assert not wanted & set(CONTROL_RANGE)


# ---------------------------------------------------------------------------
# Holding, refreshing, releasing
# ---------------------------------------------------------------------------


async def test_select_holds_and_keeps_refreshing(hass, mock_hub, freezer):
    """The opposite rule from a stored parameter: re-writing is the point, so a
    repeat is never skipped. Without it the controller discards the value."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "select", 1007)
    assert hass.states.get(eid).state == NOT_CONTROLLED

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": eid, "option": "Comfort"},
        blocking=True,
    )
    assert mock_hub == [(1007, 2)]
    assert hass.states.get(eid).state == "Comfort"

    await advance(hass, freezer, REFRESH.total_seconds() + 5)
    assert mock_hub == [(1007, 2), (1007, 2)]


async def test_releasing_a_select_writes_nothing(hass, mock_hub, freezer):
    """Writing 0 would be a command, not a release - the manual documents
    0 = Economy on 1007 and 0 = Off on the zone modes."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "select", 1007)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": eid, "option": "Comfort"},
        blocking=True,
    )
    mock_hub.clear()

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": eid, "option": NOT_CONTROLLED},
        blocking=True,
    )
    assert mock_hub == []
    assert hass.states.get(eid).state == NOT_CONTROLLED

    # And the refresh stops with it.
    await advance(hass, freezer, REFRESH.total_seconds() + 5)
    assert mock_hub == []


async def test_number_reads_unknown_until_held_and_zero_releases(
    hass, mock_hub
):
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "number", 1002)
    assert hass.states.get(eid).state == "unknown"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": eid, "value": 60},
        blocking=True,
    )
    assert mock_hub == [(1002, 600)]  # 0.1 rps, borrowed from 61572
    assert hass.states.get(eid).state == "60.0"

    mock_hub.clear()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": eid, "value": 0}, blocking=True
    )
    assert mock_hub == []
    assert hass.states.get(eid).state == "unknown"


async def test_a_negative_offset_survives_the_round_trip(hass, mock_hub):
    """1023-1027 are the controls where a negative value is meaningful, and
    1022 documents -1 = Reduced, so the held word is read back signed."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "number", 1023)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": eid, "value": -2.5}, blocking=True
    )
    assert mock_hub == [(1023, 0xFFE7)]  # -25 as a 16-bit word
    assert hass.states.get(eid).state == "-2.5"


async def test_a_hold_the_controller_has_expired_is_dropped(hass, mock_hub, freezer):
    """The controller discards a control after five minutes without a write, so
    a link that has been down that long means HA is no longer holding anything
    - and should stop claiming it is."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "select", 1007)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": eid, "option": "Normal"},
        blocking=True,
    )
    assert hass.states.get(eid).state == "Normal"

    async def dead(self, address, value):
        raise CtcConnectionError("link down")

    with patch(
        "custom_components.ctc_bms.hub.CtcHub.async_write_register", dead
    ):
        # One refresh inside the window: still held, still trying.
        await advance(hass, freezer, REFRESH.total_seconds() + 5)
        assert hass.states.get(eid).state == "Normal"
        await advance(hass, freezer, EXPIRY.total_seconds() + 5)
    assert hass.states.get(eid).state == NOT_CONTROLLED


async def test_a_failed_write_does_not_pretend_to_hold(hass, mock_hub):
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "select", 1007)

    async def dead(self, address, value):
        raise CtcConnectionError("link down")

    with patch(
        "custom_components.ctc_bms.hub.CtcHub.async_write_register", dead
    ):
        with pytest.raises(Exception):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": eid, "option": "Comfort"},
                blocking=True,
            )
    assert hass.states.get(eid).state == NOT_CONTROLLED


async def test_nothing_is_re_asserted_across_a_reload(hass, mock_hub, freezer):
    """The five-minute expiry is the fail-safe; honouring it means never
    commanding a heating system with no human in the loop."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "select", 1007)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": eid, "option": "Comfort"},
        blocking=True,
    )
    mock_hub.clear()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert mock_hub == []
    assert hass.states.get(eid).state == NOT_CONTROLLED

    await advance(hass, freezer, REFRESH.total_seconds() + 5)
    assert mock_hub == []


# ---------------------------------------------------------------------------
# Register 1100: eight inputs and a SmartGrid state, in one word
# ---------------------------------------------------------------------------


async def test_smartgrid_needs_its_bits_configured(hass, mock_hub):
    """The manual is explicit that a terminal's DI number is set in the
    controller's own menus, so it cannot be looked up - only told to us."""
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    assert not registry.async_get_entity_id(
        "select", DOMAIN, f"{entry.entry_id}_1100_smartgrid"
    )


@pytest.mark.parametrize(
    ("option", "word"),
    [
        ("Block", 1 << 6),
        ("Low price", 1 << 7),
        ("High capacity", (1 << 6) | (1 << 7)),
    ],
)
async def test_smartgrid_writes_the_manuals_truth_table(
    hass, mock_hub, option, word
):
    entry = await setup_entry(
        hass, **{CONF_SMARTGRID_A: "6", CONF_SMARTGRID_B: "7"}
    )
    eid = entity_id(hass, entry, "select", "1100_smartgrid")
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": eid, "option": option},
        blocking=True,
    )
    assert mock_hub == [(VDI_REGISTER, word)]
    assert hass.states.get(eid).state == option


async def test_smartgrid_normal_releases_the_word(hass, mock_hub):
    """Both inputs open is the documented resting state, and 0 on 1100 means
    exactly that - the one control worth writing to release, so a block does
    not linger for five minutes after being cleared."""
    entry = await setup_entry(
        hass, **{CONF_SMARTGRID_A: "6", CONF_SMARTGRID_B: "7"}
    )
    eid = entity_id(hass, entry, "select", "1100_smartgrid")
    await hass.services.async_call(
        "select", "select_option", {"entity_id": eid, "option": "Block"},
        blocking=True,
    )
    mock_hub.clear()

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": eid, "option": "None/Normal"},
        blocking=True,
    )
    assert mock_hub == [(VDI_REGISTER, 0)]
    assert hass.states.get(eid).state == "None/Normal"


async def test_the_inputs_and_smartgrid_share_one_word(hass, mock_hub):
    """All nine entities on 1100 read-modify-write the same held word, so they
    can never fight over it."""
    entry = await setup_entry(
        hass, **{CONF_SMARTGRID_A: "6", CONF_SMARTGRID_B: "7"}
    )
    sg = entity_id(hass, entry, "select", "1100_smartgrid")
    di3 = entity_id(hass, entry, "switch", "1100_di3")
    await enable(hass, entry, di3)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": di3}, blocking=True
    )
    assert mock_hub == [(VDI_REGISTER, 1 << 3)]

    await hass.services.async_call(
        "select", "select_option", {"entity_id": sg, "option": "Block"},
        blocking=True,
    )
    assert mock_hub[-1] == (VDI_REGISTER, (1 << 3) | (1 << 6))
    assert hass.states.get(di3).state == "on"
    assert hass.states.get(sg).state == "Block"

    # Clearing SmartGrid leaves DI3 closed, so the register stays held.
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": sg, "option": "None/Normal"},
        blocking=True,
    )
    assert mock_hub[-1] == (VDI_REGISTER, 1 << 3)
    assert hass.states.get(di3).state == "on"


async def test_the_inputs_ship_disabled(hass, mock_hub):
    """Which terminal function each input carries is configured in the
    controller's own menus; most installs use none of them."""
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    for bit in range(8):
        entry_ = registry.async_get(
            entity_id(hass, entry, "switch", f"1100_di{bit}")
        )
        assert entry_.disabled_by is er.RegistryEntryDisabler.INTEGRATION
