"""CTC heat pump integration over the BMS Modbus TCP protocol."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import CONF_DEVICE_ID, DEFAULT_DEVICE_ID, DEFAULT_PORT
from .coordinator import CtcConfigEntry, CtcCoordinator
from .hub import CtcConnectionError, CtcHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VALVE,
]


async def async_setup_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    hub = CtcHub(
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
    )
    try:
        await hub.async_connect()
        await hub.async_probe()
    except CtcConnectionError as err:
        hub.close()
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = CtcCoordinator(hass, entry, hub)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _drop_reclassified_entities(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


def _drop_reclassified_entities(
    hass: HomeAssistant, entry: CtcConfigEntry, coordinator: CtcCoordinator
) -> None:
    """Remove registry entries for registers that moved to another platform.

    A register's unique_id is its number, so moving one (a 0/1 status sensor
    becoming a binary_sensor, a diverter becoming a valve) would otherwise
    leave the old entity behind for ever as an unavailable "restored" entry -
    the entity registry keeps entries an integration stops providing. Its
    recorded history stays in the database under the old entity_id; anything
    referencing that id needs updating by hand.
    """
    registry = er.async_get(hass)
    wanted = {
        f"{entry.entry_id}_{reg.number}": coordinator.platform_for(key, reg)
        for key, reg in coordinator.entity_registers()
    }
    for existing in er.async_entries_for_config_entry(registry, entry.entry_id):
        platform = wanted.get(existing.unique_id)
        if platform is not None and existing.domain != platform:
            _LOGGER.info(
                "Register %s is now a %s: removing %s",
                existing.unique_id.rsplit("_", 1)[-1],
                platform,
                existing.entity_id,
            )
            registry.async_remove(existing.entity_id)


async def _async_options_updated(
    hass: HomeAssistant, entry: CtcConfigEntry
) -> None:
    """Options changed: rebuild everything from scratch."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.hub.close()
    return unloaded
