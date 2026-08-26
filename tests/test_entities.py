"""Full setup: devices, sensor decode paths and number writes end-to-end."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.ctc_bms.const import (
    CONF_DEVICE_ID,
    CONF_HEAT_PUMPS,
    CONF_MODEL,
    CONF_SUBSYSTEMS,
    CONF_ZONES,
    DOMAIN,
)
from custom_components.ctc_bms.registers import ZONE_FIELDS


async def setup_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1.2.3.4:502:1",
        data={
            CONF_HOST: "1.2.3.4",
            CONF_PORT: 502,
            CONF_DEVICE_ID: 1,
            CONF_HEAT_PUMPS: [1],
            CONF_ZONES: [1],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_id(hass, entry, platform: str, register: int) -> str:
    registry = er.async_get(hass)
    eid = registry.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{register}"
    )
    assert eid, f"no {platform} entity for register {register}"
    return eid


async def test_devices_created(hass, mock_hub):
    # No subsystems key stored: an entry predating them keeps everything, so
    # upgrading never silently removes entities.
    entry = await setup_entry(hass)
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    names = {d.name for d in devices}
    assert names == {
        "CTC Heat Pump System",
        "CTC Heat Pump 1",
        "CTC Heating System 1",
        "CTC Hot Water",
        "CTC Solar",
        "CTC Pool",
        "CTC Cooling",
        "CTC Ventilation",
        "CTC Additional Heat",
    }
    system = next(d for d in devices if d.name == "CTC Heat Pump System")
    for name in ("CTC Heat Pump 1", "CTC Hot Water", "CTC Pool"):
        child = next(d for d in devices if d.name == name)
        assert child.via_device_id == system.id


async def test_current_sensor_is_created_disabled_in_amps(hass, mock_hub):
    """3-phase current: registered but disabled, and its unit is corrected."""
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    reg_entry = registry.async_get(entity_id(hass, entry, "sensor", 62171))
    assert reg_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert reg_entry.unit_of_measurement == "A"
    assert reg_entry.original_device_class == "current"
    # Disabled => no state is produced until the user enables it.
    assert hass.states.get(reg_entry.entity_id) is None


async def test_immersion_power_disabled_but_still_kw(hass, mock_hub):
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    reg_entry = registry.async_get(entity_id(hass, entry, "sensor", 62168))
    assert reg_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert reg_entry.unit_of_measurement == "kW"


async def test_subsystems_can_be_disabled(hass, mock_hub):
    """Unticking a subsystem removes its device, entities and poll registers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1.2.3.4:502:1",
        data={
            CONF_HOST: "1.2.3.4",
            CONF_PORT: 502,
            CONF_DEVICE_ID: 1,
            CONF_HEAT_PUMPS: [1],
            CONF_ZONES: [1],
        },
        options={CONF_SUBSYSTEMS: ["DHW"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), entry.entry_id
    )
    names = {d.name for d in devices}
    assert "CTC Hot Water" in names
    assert "CTC Pool" not in names

    registry = er.async_get(hass)
    # 62179 poolTemp: no entity, and the register left the poll set.
    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_62179"
        )
        is None
    )
    assert 62179 not in entry.runtime_data._wanted


async def test_model_names_the_controller_device(hass, mock_hub):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1.2.3.4:502:1",
        data={
            CONF_HOST: "1.2.3.4",
            CONF_PORT: 502,
            CONF_DEVICE_ID: 1,
            CONF_HEAT_PUMPS: [1],
            CONF_ZONES: [1],
            CONF_MODEL: "ecologic_m",
            CONF_SUBSYSTEMS: ["DHW", "AddHeat"],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), entry.entry_id
    )
    system = next(d for d in devices if d.name == "CTC Heat Pump System")
    assert system.model == "CTC EcoLogic M"
    # The real EcoLogic M install: hot water and additional heat, nothing else.
    assert {d.name for d in devices if d.name.startswith("CTC")} == {
        "CTC Heat Pump System",
        "CTC Heat Pump 1",
        "CTC Heating System 1",
        "CTC Hot Water",
        "CTC Additional Heat",
    }


async def test_system_register_lands_on_its_subsystem_device(hass, mock_hub):
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    # 62003 sDHWTemp keeps its unique_id but now hangs off the Hot Water device.
    eid = entity_id(hass, entry, "sensor", 62003)
    device = devices.async_get(registry.async_get(eid).device_id)
    assert device.name == "CTC Hot Water"
    # ... and 62000 sOutsideTemp stays on the controller.
    outside = registry.async_get(entity_id(hass, entry, "sensor", 62000))
    assert devices.async_get(outside.device_id).name == "CTC Heat Pump System"


async def test_heating_curves_follow_their_zone(hass, mock_hub):
    """The hcN curve registers are grouped onto zone N's device.

    They produce no entities yet - every one is RW and none is a curated
    setpoint - so this asserts the grouping rather than the entity registry.
    Zone 2 isn't configured, so its curves are dropped entirely.
    """
    entry = await setup_entry(hass)
    device_regs = entry.runtime_data.device_regs
    assert 61675 in {r.number for r in device_regs["Zone1"]}
    assert "Zone2" not in device_regs
    assert 61685 not in {
        r.number for regs in device_regs.values() for r in regs
    }


async def test_sensor_values(hass, mock_hub):
    entry = await setup_entry(hass)
    # negative S16 temperature
    outside = hass.states.get(entity_id(hass, entry, "sensor", 62000))
    assert float(outside.state) == -5.3
    assert outside.attributes["unit_of_measurement"] == "°C"
    assert outside.attributes["device_class"] == "temperature"
    # documented status -> the state itself, not the raw number
    assert hass.states.get(entity_id(hass, entry, "sensor", 62017)).state == (
        "Compressor on, heating"
    )
    # 32-bit LSB-first counter
    assert (
        float(hass.states.get(entity_id(hass, entry, "sensor", 62214)).state)
        == 12345
    )
    # sentinel -> unknown (the register answered; the sensor is not fitted)
    assert hass.states.get(entity_id(hass, entry, "sensor", 62203)).state == (
        "unknown"
    )
    # idle-compressor 0 is a real reading, not unknown
    assert float(hass.states.get(entity_id(hass, entry, "sensor", 62037)).state) == 0


async def test_unpopulated_dhw_temperatures_read_unknown(hass, mock_hub):
    """0 C in a hot water tank is no reading, not a reading of zero.

    The controller populates whichever tank temperatures its arrangement has
    and parks the rest at 0 - not at the -9999/-10000 sentinel. On an EcoLogic
    M only 62276 is live, so "Temperature" (62003) led the Hot Water device
    with a headline 0.0 C while the water was at 61.
    """
    entry = await setup_entry(hass)
    for number in (62002, 62003, 62275):
        assert hass.states.get(
            entity_id(hass, entry, "sensor", number)
        ).state == "unknown", number
    # The one the tank actually reports through still decodes.
    assert float(
        hass.states.get(entity_id(hass, entry, "sensor", 62276)).state
    ) == 61.0


async def test_friendly_names_compose_with_the_device(hass, mock_hub):
    """has_entity_name: HA prepends the device, so the name must not repeat it."""
    entry = await setup_entry(hass)
    names = {
        register: hass.states.get(
            entity_id(hass, entry, "sensor", register)
        ).attributes["friendly_name"]
        for register in (62193, 62203, 62003, 62301)
    }
    assert names == {
        62193: "CTC Heat Pump 1 RPS",  # not "... Curr RPS"
        62203: "CTC Heating System 1 Room temperature",  # not "... Temp Curr"
        62003: "CTC Hot Water Temperature",  # not "CTC Hot Water DHW Temp"
        # not "... Sgmode 0 none normal 1 block 2 low price"
        62301: "CTC Heat Pump System Smart grid mode",
    }
    # 62170 ships disabled, so it has no state - but it is still the register
    # the manual calls the *maximum* current, not a live reading.
    registry = er.async_get(hass)
    reg_entry = registry.async_get(entity_id(hass, entry, "sensor", 62170))
    assert reg_entry.original_name == "Maximum current"


async def test_primary_system_flow_is_a_flow_rate(hass, mock_hub):
    """"Primary system flow" is l/min - unlike the "Primary flow" temperatures."""
    entry = await setup_entry(hass)
    flow = hass.states.get(entity_id(hass, entry, "sensor", 62291))
    assert float(flow.state) == 13.2
    assert flow.attributes["unit_of_measurement"] == "L/min"
    assert flow.attributes["device_class"] == "volume_flow_rate"
    # the zone "Primary flow" registers must not have been swept up with it
    assert {f.unit for f in ZONE_FIELDS if f.field.startswith("PrimaryFlow")} == {
        "",
        "°C",
    }


async def test_number_write(hass, mock_hub):
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "number", 61501)  # DHW manual stop temp
    assert float(hass.states.get(eid).state) == 50.0
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": eid, "value": 55.0},
        blocking=True,
    )
    await hass.async_block_till_done()
    # encoded with the register scale (0.1) and written via the hub
    assert mock_hub == [(61501, 550)]
    assert float(hass.states.get(eid).state) == 55.0


async def test_select_write(hass, mock_hub):
    """sDHWMode is an enum: a select on the Hot Water device, not a number."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "select", 61500)
    state = hass.states.get(eid)
    assert state.state == "Normal"
    assert state.attributes["options"] == ["Economy", "Normal", "Comfort"]

    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    device = devices.async_get(registry.async_get(eid).device_id)
    assert device.name == "CTC Hot Water"
    # ... and it never became a number.
    assert (
        registry.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_61500")
        is None
    )

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": eid, "option": "Comfort"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert mock_hub == [(61500, 2)]
    assert hass.states.get(eid).state == "Comfort"


async def test_select_unknown_value_is_not_an_option(
    hass, mock_hub, fake_registers
):
    """A raw value outside the documented set reads as unknown, not a crash."""
    fake_registers[61500] = 7  # mock_hub restores this after the test
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "select", 61500)
    assert hass.states.get(eid).state == "unknown"


async def test_enum_sensor(hass, mock_hub, fake_registers):
    """A documented status reads as its state, with the legend as options."""
    fake_registers[62005] = 5  # sStatus = DHW
    entry = await setup_entry(hass)
    state = hass.states.get(entity_id(hass, entry, "sensor", 62005))
    assert state.state == "DHW"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"][:4] == [
        "HP upper",
        "HP lower",
        "Add",
        "HP + Add",
    ]
    # An enum sensor must carry neither a unit nor a state class.
    assert "unit_of_measurement" not in state.attributes
    assert "state_class" not in state.attributes


async def test_enum_sensor_gap_reads_unknown(hass, mock_hub, fake_registers):
    """HP status jumps 8 -> 30; an undocumented value is not invented."""
    fake_registers[62017] = 12
    entry = await setup_entry(hass)
    assert hass.states.get(entity_id(hass, entry, "sensor", 62017)).state == (
        "unknown"
    )
    fake_registers[62017] = 32
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, entry, "sensor", 62017)).state == (
        "Communication error"
    )


async def test_zone_shunt_state_is_an_enum_not_a_valve(hass, mock_hub):
    """The modulating shunts report their documented drive direction."""
    entry = await setup_entry(hass)
    state = hass.states.get(entity_id(hass, entry, "sensor", 62308))
    assert state.state == "Close"  # register reads 0
    assert state.attributes["options"] == ["Close", "Inactive", "Open"]


async def test_zone_mode_select_is_an_array_field(hass, mock_hub):
    """The enum tables reach the HP/Zone arrays, not just the flat map."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "select", 61542)  # Zone1 Mode
    state = hass.states.get(eid)
    assert state.state == "Auto"
    assert state.attributes["options"] == ["Auto", "On", "Off"]
    device = dr.async_get(hass).async_get(
        er.async_get(hass).async_get(eid).device_id
    )
    assert device.name == "CTC Heating System 1"

    await hass.services.async_call(
        "select", "select_option", {"entity_id": eid, "option": "Off"}, blocking=True
    )
    await hass.async_block_till_done()
    assert mock_hub == [(61542, 2)]


async def test_heating_program_select_follows_its_zone(hass, mock_hub):
    """A flat register shown on a Zone device is still keyed by number."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "select", 61671)  # HC1 heating program
    assert hass.states.get(eid).state == "Normal"
    assert hass.states.get(eid).attributes["options"] == [
        "Economy",
        "Normal",
        "Comfort",
        "Custom",
    ]
    device = dr.async_get(hass).async_get(
        er.async_get(hass).async_get(eid).device_id
    )
    assert device.name == "CTC Heating System 1"


async def test_hp_blocked_switch_keeps_the_register_polarity(hass, mock_hub):
    """"0=Blocked, 1=Allowed": the switch is on when the pump IS blocked."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "switch", 61521)
    assert hass.states.get(eid).state == "off"  # register reads 1 = Allowed

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": eid}, blocking=True
    )
    await hass.async_block_till_done()
    assert mock_hub == [(61521, 0)]  # on => blocked => writes 0
    assert hass.states.get(eid).state == "on"


async def test_write_is_skipped_when_the_register_already_matches(
    hass, mock_hub
):
    """The controller's parameters have a limited write-cycle count, so a
    write that would not change the register never reaches the hub.

    HA does not suppress a service call matching current state, so without this
    an automation re-asserting a steady value would burn a cycle every run.
    """
    entry = await setup_entry(hass)

    # 61501 already reads 50.0, 61500 already reads Normal, 61521 already
    # reads 1 = Allowed i.e. the switch is off.
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id(hass, entry, "number", 61501), "value": 50.0},
        blocking=True,
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id(hass, entry, "select", 61500), "option": "Normal"},
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id(hass, entry, "switch", 61521)},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert mock_hub == []


async def test_write_below_the_register_step_is_not_a_write(hass, mock_hub):
    """Raw words are compared, not engineering values: 50.02 C encodes to the
    word 61501 already holds (scale 0.1), so there is nothing to write."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "number", 61501)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": eid, "value": 50.02}, blocking=True
    )
    await hass.async_block_till_done()
    assert mock_hub == []
    assert float(hass.states.get(eid).state) == 50.0


async def test_a_real_change_still_writes_every_time(hass, mock_hub):
    """The guard drops no-ops only - it must not swallow an actual change.

    A -> B -> A back to back is the case that catches a guard comparing
    against the coordinator alone: the read-back refresh is debounced, so all
    three writes see pre-write data and the last one looks like a no-op while
    the register actually holds B.
    """
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "number", 61501)
    for value in (55.0, 50.0, 55.0):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": eid, "value": value}, blocking=True
        )
        await hass.async_block_till_done()
    assert mock_hub == [(61501, 550), (61501, 500), (61501, 550)]


async def test_switch_reports_unknown_for_an_undocumented_value(
    hass, mock_hub, fake_registers
):
    fake_registers[61521] = 5
    entry = await setup_entry(hass)
    assert hass.states.get(entity_id(hass, entry, "switch", 61521)).state == (
        "unknown"
    )


async def test_hot_water_valve(hass, mock_hub):
    """The diverter is a read-only valve, and no longer a sensor."""
    entry = await setup_entry(hass)
    eid = entity_id(hass, entry, "valve", 62313)
    state = hass.states.get(eid)
    assert state.state == "open"  # register reads 1
    assert state.attributes["device_class"] == "water"
    assert state.attributes["supported_features"] == 0
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_62313")
        is None
    )
    # The modulating shunt stays a sensor: "Inactive" has no valve state.
    assert entity_id(hass, entry, "sensor", 62308)


async def test_boolean_statuses_are_binary_sensors(hass, mock_hub):
    entry = await setup_entry(hass)
    assert hass.states.get(entity_id(hass, entry, "binary_sensor", 62304)).state == (
        "on"
    )
    solar = hass.states.get(entity_id(hass, entry, "binary_sensor", 62181))
    assert solar.state == "off"
    assert solar.attributes["device_class"] == "running"
    # A 2-bit relay field and a 0-100 pump are NOT booleans.
    assert entity_id(hass, entry, "sensor", 62315)
    assert entity_id(hass, entry, "sensor", 62323)


async def test_dhw_circulation_pump_is_a_binary_sensor(hass, mock_hub):
    """62016 is on/off, despite the '%' the generator infers from its name.

    Its sibling 62323 is documented "DHWPump: 0-100" and this one is bare; the
    field only ever shows 0 or 1. Reclassifying it drops the old sensor.
    """
    entry = await setup_entry(hass)
    state = hass.states.get(entity_id(hass, entry, "binary_sensor", 62016))
    assert state.state == "on"
    assert state.attributes["device_class"] == "running"
    assert "unit_of_measurement" not in state.attributes
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_62016")
        is None
    )


async def test_undocumented_boolean_stays_read_only(hass, mock_hub):
    """Pool enable is RW, but nothing may write it until its values are known."""
    entry = await setup_entry(hass)
    assert hass.states.get(entity_id(hass, entry, "sensor", 61658)).state == "1"
    registry = er.async_get(hass)
    for platform in ("switch", "number", "select"):
        assert (
            registry.async_get_entity_id(
                platform, DOMAIN, f"{entry.entry_id}_61658"
            )
            is None
        )


async def test_reclassified_entity_is_removed(hass, mock_hub):
    """An install that had 62313 as a sensor loses it when it becomes a valve."""
    registry = er.async_get(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1.2.3.4:502:1",
        data={
            CONF_HOST: "1.2.3.4",
            CONF_PORT: 502,
            CONF_DEVICE_ID: 1,
            CONF_HEAT_PUMPS: [1],
            CONF_ZONES: [1],
        },
    )
    entry.add_to_hass(hass)
    stale = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_62313", config_entry=entry
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(stale.entity_id) is None
    assert entity_id(hass, entry, "valve", 62313)


async def test_setpoints_can_be_disabled(hass, mock_hub):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1.2.3.4:502:1",
        data={
            CONF_HOST: "1.2.3.4",
            CONF_PORT: 502,
            CONF_DEVICE_ID: 1,
            CONF_HEAT_PUMPS: [1],
            CONF_ZONES: [1],
        },
        options={"setpoints": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "number", DOMAIN, f"{entry.entry_id}_61501"
        )
        is None
    )
    # ... and the enum selects go with them: nothing writable is created.
    assert (
        registry.async_get_entity_id(
            "select", DOMAIN, f"{entry.entry_id}_61500"
        )
        is None
    )
    assert 61500 not in entry.runtime_data._wanted


async def test_an_entry_with_no_stored_choice_keeps_its_writable_entities(
    hass, mock_hub
):
    """New entries default to read-only, but upgrading must not remove
    entities an existing install already has.

    setup_entry() stores no 'setpoints' key, exactly like an entry created
    before the setup step existed - so the fallback has to stay True even
    though the config flow now defaults the choice to False.
    """
    entry = await setup_entry(hass)
    assert entry.runtime_data.setpoints_enabled is True
    assert entity_id(hass, entry, "number", 61501)
    assert entity_id(hass, entry, "select", 61500)
    assert entity_id(hass, entry, "switch", 61521)
