"""CTC heat pump integration over the BMS Modbus TCP protocol."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_DEVICE_ID, DEFAULT_DEVICE_ID, DEFAULT_PORT
from .coordinator import CtcConfigEntry, CtcCoordinator
from .hub import CtcConnectionError, CtcHub

PLATFORMS = [Platform.NUMBER, Platform.SENSOR]


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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


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
