"""Diagnostics: a raw register snapshot plus what the integration made of it."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import CtcConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CtcConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry_data": {
            k: v for k, v in entry.data.items() if k != "host"
        },
        "options": dict(entry.options),
        "heat_pumps": coordinator.heat_pumps,
        "zones": coordinator.zones,
        "dead_addresses": sorted(coordinator.hub.dead_addresses),
        "last_update_success": coordinator.last_update_success,
        "registers": {
            str(addr): word
            for addr, word in sorted((coordinator.data or {}).items())
        },
    }
