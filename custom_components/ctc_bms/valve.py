"""Two-position diverter valves as valve entities.

Read-only by necessity: the BMS map has no writable valve register, so these
report position and advertise no OPEN/CLOSE feature. The modulating shunts are
deliberately not here - see VALVE_SYSTEM in const.py for why drive direction
("0=Close, 1=Inactive, 2=Open") does not fit this domain.
"""

from __future__ import annotations

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import VALVE_SYSTEM
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
        CtcValve(coordinator, device_key, reg)
        for device_key, reg in coordinator.entity_registers()
        if coordinator.platform_for(device_key, reg) == "valve"
    )


class CtcValve(CtcEntity, ValveEntity):
    # A diverter is either way over; it has no intermediate position, and
    # nothing here can drive it.
    _attr_reports_position = False
    _attr_supported_features = ValveEntityFeature(0)

    def __init__(
        self, coordinator: CtcCoordinator, device_key: str, reg: Reg
    ) -> None:
        super().__init__(coordinator, device_key, reg)
        self._open_value, device_class = VALVE_SYSTEM[reg.number]
        self._attr_device_class = ValveDeviceClass(device_class)

    @property
    def is_closed(self) -> bool | None:
        value = self.decoded_value()
        return None if value is None else int(value) != self._open_value
