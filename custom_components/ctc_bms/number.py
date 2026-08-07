"""Writable setpoints (a curated subset of the RW registers) as numbers.

These write to the controller's stored parameters, which have a limited number
of write cycles - see the warning on CtcEntity.async_write_raw, which is the
write path and skips a write that would not change the register.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
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
        CtcNumber(coordinator, device_key, reg)
        for device_key, reg in coordinator.entity_registers()
        if coordinator.platform_for(device_key, reg) == "number"
    )


class CtcNumber(CtcEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: CtcCoordinator, device_key: str, reg: Reg
    ) -> None:
        super().__init__(coordinator, device_key, reg)
        low, high, step = coordinator.setpoint_limits(device_key, reg)
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        if reg.unit == "°C":
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif reg.unit:
            self._attr_native_unit_of_measurement = reg.unit

    @property
    def native_value(self) -> float | None:
        return self.decoded_value()

    async def async_set_native_value(self, value: float) -> None:
        await self.async_write_raw(encode_value(self.reg, value))
