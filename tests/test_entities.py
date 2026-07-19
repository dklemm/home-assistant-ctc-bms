"""Full setup: devices, sensor decode paths and number writes end-to-end."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.ctc_bms.const import (
    CONF_DEVICE_ID,
    CONF_HEAT_PUMPS,
    CONF_ZONES,
    DOMAIN,
)


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
    entry = await setup_entry(hass)
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    names = {d.name for d in devices}
    assert names == {
        "CTC Heat Pump System",
        "CTC Heat Pump 1",
        "CTC Heating System 1",
    }
    system = next(d for d in devices if d.name == "CTC Heat Pump System")
    hp1 = next(d for d in devices if d.name == "CTC Heat Pump 1")
    assert hp1.via_device_id == system.id


async def test_sensor_values(hass, mock_hub):
    entry = await setup_entry(hass)
    # negative S16 temperature
    outside = hass.states.get(entity_id(hass, entry, "sensor", 62000))
    assert float(outside.state) == -5.3
    assert outside.attributes["unit_of_measurement"] == "°C"
    assert outside.attributes["device_class"] == "temperature"
    # plain status
    assert hass.states.get(entity_id(hass, entry, "sensor", 62017)).state == "3"
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
