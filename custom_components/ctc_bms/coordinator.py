"""Polling coordinator for the CTC BMS integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CONF_HEAT_PUMPS,
    CONF_SETPOINTS,
    CONF_ZONES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    SETPOINT_HP_FIELDS,
    SETPOINT_SYSTEM,
    SETPOINT_ZONE_FIELDS,
)
from .hub import CtcConnectionError, CtcHub
from .registers import (
    SYSTEM_REGISTERS,
    Reg,
    registers_for_hp,
    registers_for_zone,
)

_LOGGER = logging.getLogger(__name__)

type CtcConfigEntry = ConfigEntry[CtcCoordinator]


def _option(entry: ConfigEntry, key: str, default):
    """An option, falling back to what the config flow detected/stored."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


class CtcCoordinator(DataUpdateCoordinator[dict[int, int]]):
    """Polls every register the created entities need in one batched pass.

    `data` is the raw {address: word} map; decoding stays in the entities so
    the coordinator remains dumb and testable.
    """

    def __init__(
        self, hass: HomeAssistant, entry: CtcConfigEntry, hub: CtcHub
    ) -> None:
        self.hub = hub
        scan = _option(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan),
        )

        # Options-flow multiselects store strings; detection stores ints.
        self.heat_pumps: list[int] = sorted(
            int(n) for n in _option(entry, CONF_HEAT_PUMPS, [1])
        )
        self.zones: list[int] = sorted(
            int(n) for n in _option(entry, CONF_ZONES, [1])
        )
        self.setpoints_enabled: bool = _option(entry, CONF_SETPOINTS, True)

        # The map split into HA devices, restricted to the hardware that is
        # actually fitted (per config-flow detection / options override).
        self.device_regs: dict[str, list[Reg]] = {
            "System": sorted(SYSTEM_REGISTERS, key=lambda r: r.number)
        }
        for n in self.heat_pumps:
            self.device_regs[f"HP{n}"] = registers_for_hp(n)
        for n in self.zones:
            self.device_regs[f"Zone{n}"] = registers_for_zone(n)

        # Poll read-only registers plus the RW ones that become number
        # entities; the ~180 other setpoints would only bloat the poll.
        self._wanted: set[int] = set()
        for _key, reg in self.entity_registers():
            for i in range(reg.count):
                self._wanted.add(reg.number + i)

    def entity_registers(self) -> list[tuple[str, Reg]]:
        """(device_key, Reg) for every register that becomes an entity."""
        out: list[tuple[str, Reg]] = []
        for key, regs in self.device_regs.items():
            for reg in regs:
                if reg.access == "R" or self._is_setpoint(key, reg):
                    out.append((key, reg))
        return out

    def _is_setpoint(self, key: str, reg: Reg) -> bool:
        if not self.setpoints_enabled or reg.access != "RW":
            return False
        if key == "System":
            return reg.number in SETPOINT_SYSTEM
        field = reg.name.split(" ", 1)[1] if " " in reg.name else reg.name
        if key.startswith("HP"):
            return field in SETPOINT_HP_FIELDS
        return field in SETPOINT_ZONE_FIELDS

    def setpoint_limits(self, key: str, reg: Reg):
        if key == "System":
            return SETPOINT_SYSTEM[reg.number]
        field = reg.name.split(" ", 1)[1]
        if key.startswith("HP"):
            return SETPOINT_HP_FIELDS[field]
        return SETPOINT_ZONE_FIELDS[field]

    def device_info(self, key: str) -> DeviceInfo:
        entry_id = self.config_entry.entry_id
        if key == "System":
            return DeviceInfo(
                identifiers={(DOMAIN, f"{entry_id}_system")},
                name="CTC Heat Pump System",
                manufacturer=MANUFACTURER,
                model="BMS controller",
            )
        if key.startswith("HP"):
            name = f"CTC Heat Pump {key.removeprefix('HP')}"
        else:
            name = f"CTC Heating System {key.removeprefix('Zone')}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{key.lower()}")},
            name=name,
            manufacturer=MANUFACTURER,
            via_device=(DOMAIN, f"{entry_id}_system"),
        )

    async def _async_update_data(self) -> dict[int, int]:
        try:
            # Hard cap: a degraded link bisecting through timeouts must not
            # overrun the poll interval.
            async with asyncio.timeout(
                max(self.update_interval.total_seconds() - 1, 10)
            ):
                data = await self.hub.async_read_addresses(self._wanted)
        except CtcConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except TimeoutError as err:
            raise UpdateFailed(
                "Poll overran its interval (degraded link?)"
            ) from err
        if not data:
            raise UpdateFailed("Controller returned no registers")
        return data
