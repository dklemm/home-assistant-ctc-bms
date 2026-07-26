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
    BINARY_SYSTEM,
    CONF_HEAT_PUMPS,
    CONF_MODEL,
    CONF_SETPOINTS,
    CONF_SUBSYSTEMS,
    CONF_ZONES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENUM_HP_FIELDS,
    ENUM_SYSTEM,
    ENUM_ZONE_FIELDS,
    MANUFACTURER,
    READ_ONLY_RW,
    SELECT_HP_FIELDS,
    SELECT_SYSTEM,
    SELECT_ZONE_FIELDS,
    SETPOINT_HP_FIELDS,
    SETPOINT_SYSTEM,
    SETPOINT_ZONE_FIELDS,
    SWITCH_HP_FIELDS,
    SWITCH_SYSTEM,
    SWITCH_ZONE_FIELDS,
    VALVE_SYSTEM,
)
from .groups import SUBSYSTEMS, group_for
from .hub import CtcConnectionError, CtcHub
from .models import DEFAULT_MODEL, MODELS
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
        self.model: str = _option(entry, CONF_MODEL, DEFAULT_MODEL)
        # Entries created before subsystems existed have no stored list; keep
        # every subsystem so an upgrade never silently removes entities.
        self.subsystems: list[str] = [
            key
            for key in SUBSYSTEMS
            if key in _option(entry, CONF_SUBSYSTEMS, list(SUBSYSTEMS))
        ]

        # The map split into HA devices, restricted to the hardware that is
        # actually fitted (per config-flow detection / options override).
        self.device_regs: dict[str, list[Reg]] = {"System": []}
        for n in self.heat_pumps:
            self.device_regs[f"HP{n}"] = registers_for_hp(n)
        for n in self.zones:
            self.device_regs[f"Zone{n}"] = registers_for_zone(n)
        for key in self.subsystems:
            self.device_regs[key] = []

        # Registers for hardware the user doesn't have - an unticked subsystem,
        # a zone's heating curve - are dropped here, so they never reach an
        # entity and never enter the poll.
        for reg in SYSTEM_REGISTERS:
            key = group_for(reg)
            if key in self.device_regs:
                self.device_regs[key].append(reg)
        for regs in self.device_regs.values():
            regs.sort(key=lambda r: r.number)

        # Poll read-only registers plus the RW ones that become writable
        # entities; the ~190 other setpoints would only bloat the poll.
        self._wanted: set[int] = set()
        for _key, reg in self.entity_registers():
            for i in range(reg.count):
                self._wanted.add(reg.number + i)

    def entity_registers(self) -> list[tuple[str, Reg]]:
        """(device_key, Reg) for every register that becomes an entity."""
        return [
            (key, reg)
            for key, regs in self.device_regs.items()
            for reg in regs
            if self.platform_for(key, reg) is not None
        ]

    def platform_for(self, key: str, reg: Reg) -> str | None:
        """Which HA platform this register becomes, or None for no entity.

        The one gate on entity creation, and the reason a writable register can
        never appear on two platforms. Read-only registers all become entities;
        writable ones only if they are in a curated table, because a write goes
        to a live heating system.
        """
        if reg.access == "R":
            if reg.number in VALVE_SYSTEM:
                return "valve"
            if reg.number in BINARY_SYSTEM:
                return "binary_sensor"
            return "sensor"
        # Writable, but only its value is trusted: readable regardless of the
        # setpoints option, since nothing here can write.
        if reg.number in READ_ONLY_RW:
            return "sensor"
        if not self.setpoints_enabled:
            return None
        if self.select_options(reg) is not None:
            return "select"
        if self.switch_values(reg) is not None:
            return "switch"
        if self._is_setpoint(key, reg):
            return "number"
        return None

    # Which curated table applies is a property of the register's shape, not of
    # the device it is shown on: a system register routed to a Zone device (the
    # heating curves, the hcN programs) is still looked up by number.
    def _lookup(self, reg: Reg, flat: dict, hp: dict, zone: dict):
        if reg.device == "System":
            return flat.get(reg.number)
        field = reg.name.split(" ", 1)[1] if " " in reg.name else reg.name
        table = hp if reg.device.startswith("HP") else zone
        return table.get(field)

    def select_options(self, reg: Reg) -> dict[int, str] | None:
        """{raw value: option} for an enum register, None if it isn't one."""
        if reg.access != "RW":
            return None
        return self._lookup(
            reg, SELECT_SYSTEM, SELECT_HP_FIELDS, SELECT_ZONE_FIELDS
        )

    def enum_options(self, reg: Reg) -> dict[int, str] | None:
        """{raw value: state} for a read-only register with a documented legend.

        The read-only counterpart of select_options: a select would let you
        write a status the pump computes for itself.
        """
        if reg.access != "R":
            return None
        return self._lookup(reg, ENUM_SYSTEM, ENUM_HP_FIELDS, ENUM_ZONE_FIELDS)

    def switch_values(self, reg: Reg) -> tuple[int, int] | None:
        """(on, off) raw values for a boolean register, None if it isn't one."""
        if reg.access != "RW":
            return None
        return self._lookup(
            reg, SWITCH_SYSTEM, SWITCH_HP_FIELDS, SWITCH_ZONE_FIELDS
        )

    def _is_setpoint(self, key: str, reg: Reg) -> bool:
        if reg.access != "RW":
            return False
        return (
            self._lookup(
                reg, SETPOINT_SYSTEM, SETPOINT_HP_FIELDS, SETPOINT_ZONE_FIELDS
            )
            is not None
        )

    def setpoint_limits(self, key: str, reg: Reg) -> tuple[float, float, float]:
        return self._lookup(
            reg, SETPOINT_SYSTEM, SETPOINT_HP_FIELDS, SETPOINT_ZONE_FIELDS
        )

    def device_info(self, key: str) -> DeviceInfo:
        entry_id = self.config_entry.entry_id
        if key == "System":
            return DeviceInfo(
                identifiers={(DOMAIN, f"{entry_id}_system")},
                name="CTC Heat Pump System",
                manufacturer=MANUFACTURER,
                model=MODELS[self.model].name,
            )
        if key in SUBSYSTEMS:
            name = SUBSYSTEMS[key]
        elif key.startswith("HP"):
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
