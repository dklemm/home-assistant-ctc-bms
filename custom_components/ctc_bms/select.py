"""Enum-like writable registers (modes) as selects.

A curated subset, same rationale as number.py: only registers whose full value
set the manual documents (SELECT_SYSTEM in const.py) are exposed, because these
write to a live heating system.

These write to the controller's stored parameters, which have a limited number
of write cycles - see the warning on CtcEntity.async_write_raw, which is the
write path and skips a write that would not change the register.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
        CtcSelect(coordinator, device_key, reg, coordinator.select_options(reg))
        for device_key, reg in coordinator.entity_registers()
        if coordinator.platform_for(device_key, reg) == "select"
    )


class CtcSelect(CtcEntity, SelectEntity):
    def __init__(
        self,
        coordinator: CtcCoordinator,
        device_key: str,
        reg: Reg,
        options: dict[int, str],
    ) -> None:
        super().__init__(coordinator, device_key, reg)
        self._options = options
        self._values = {option: value for value, option in options.items()}
        self._attr_options = list(options.values())

    @property
    def current_option(self) -> str | None:
        value = self.decoded_value()
        if value is None:
            return None
        # A value outside the documented set means the map is wrong; report
        # unknown rather than an option HA would reject anyway.
        return self._options.get(int(value))

    async def async_select_option(self, option: str) -> None:
        await self.async_write_raw(encode_value(self.reg, self._values[option]))
