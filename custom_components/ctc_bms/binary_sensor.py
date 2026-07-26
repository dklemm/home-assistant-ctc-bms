"""Read-only registers that are on/off rather than numeric.

Which registers those are is a judgement call about the map (BINARY_SYSTEM in
const.py), not about Home Assistant: a register holding 0-100 would read "on"
at 1% here, so only registers that are boolean by evidence belong there.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import BINARY_SYSTEM
from .coordinator import CtcConfigEntry, CtcCoordinator
from .entity import CtcEntity
from .registers import Reg


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        CtcBinarySensor(coordinator, device_key, reg)
        for device_key, reg in coordinator.entity_registers()
        if coordinator.platform_for(device_key, reg) == "binary_sensor"
    )


class CtcBinarySensor(CtcEntity, BinarySensorEntity):
    def __init__(
        self, coordinator: CtcCoordinator, device_key: str, reg: Reg
    ) -> None:
        super().__init__(coordinator, device_key, reg)
        if device_class := BINARY_SYSTEM[reg.number]:
            self._attr_device_class = BinarySensorDeviceClass(device_class)

    @property
    def is_on(self) -> bool | None:
        value = self.decoded_value()
        return None if value is None else bool(value)
