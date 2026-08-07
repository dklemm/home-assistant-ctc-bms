"""Boolean writable registers as switches.

Curated like the setpoints (SWITCH_* in const.py): these write to a live
heating system, so only registers whose two values the manual documents are
here.

These write to the controller's stored parameters, which have a limited number
of write cycles - see the warning on CtcEntity.async_write_raw, which is the
write path and skips a write that would not change the register. A switch is
the likeliest of the three to be driven on a timer ("block the heat pump when
the price is high"), and the likeliest to be told to turn on when it is on
already, which HA does not suppress.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CtcConfigEntry, CtcCoordinator
from .decode import encode_value
from .entity import CtcEntity
from .registers import Reg


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        CtcSwitch(coordinator, device_key, reg)
        for device_key, reg in coordinator.entity_registers()
        if coordinator.platform_for(device_key, reg) == "switch"
    )


class CtcSwitch(CtcEntity, SwitchEntity):
    def __init__(
        self, coordinator: CtcCoordinator, device_key: str, reg: Reg
    ) -> None:
        super().__init__(coordinator, device_key, reg)
        # The register's own polarity, not HA's: HP "Blocked" reads 0 when the
        # pump is blocked, so on_value is 0 and turning the switch on writes 0.
        self._on_value, self._off_value = coordinator.switch_values(reg)

    @property
    def is_on(self) -> bool | None:
        value = self.decoded_value()
        if value is None:
            return None
        # Neither value: the map is wrong about this register, so say unknown
        # rather than reporting "off" for something that isn't off.
        if int(value) == self._on_value:
            return True
        return False if int(value) == self._off_value else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(self._on_value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(self._off_value)

    async def _write(self, raw: int) -> None:
        await self.async_write_raw(encode_value(self.reg, raw))
