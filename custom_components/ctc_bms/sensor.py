"""Sensors for every readable CTC BMS register."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CtcConfigEntry, CtcCoordinator
from .entity import CtcEntity
from .registers import Reg

# Register unit hint -> (HA unit, device_class, state_class). Units are
# inferred from the manual's descriptions (the manual itself gives only a scale
# factor), so treat surprises here as a map problem, not an HA one.
_UNIT_MAP: dict[str, tuple] = {
    "°C": (
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
        SensorStateClass.MEASUREMENT,
    ),
    "bar": (
        UnitOfPressure.BAR,
        SensorDeviceClass.PRESSURE,
        SensorStateClass.MEASUREMENT,
    ),
    "kW": (
        UnitOfPower.KILO_WATT,
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
    ),
    "kWh": (
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL_INCREASING,
    ),
    "h": (UnitOfTime.HOURS, None, SensorStateClass.TOTAL_INCREASING),
    "%": ("%", None, SensorStateClass.MEASUREMENT),
    "rps": ("rps", None, SensorStateClass.MEASUREMENT),
    "DM": ("DM", None, SensorStateClass.MEASUREMENT),
    "days": (UnitOfTime.DAYS, None, None),
}

# Bookkeeping registers land in the Diagnostic section of the device page so
# ~250 entities per install stay navigable.
_DIAGNOSTIC_KEYWORDS = (
    "version",
    "type",
    "model",
    "func test",
    "timer",
    "operating time",
    "total operation",
    "last 24h",
    "days until",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        CtcSensor(coordinator, device_key, reg)
        for device_key, reg in coordinator.entity_registers()
        if reg.access == "R"
    )


class CtcSensor(CtcEntity, SensorEntity):
    def __init__(
        self, coordinator: CtcCoordinator, device_key: str, reg: Reg
    ) -> None:
        super().__init__(coordinator, device_key, reg)
        unit, device_class, state_class = _UNIT_MAP.get(
            reg.unit, (reg.unit or None, None, None)
        )
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        # Unitless scale-1 registers are statuses/counters/enums; a
        # "measurement" state class would pollute long-term statistics.
        if state_class is None and reg.unit == "" and reg.scale != 1.0:
            state_class = SensorStateClass.MEASUREMENT
        self._attr_state_class = state_class
        if reg.scale in (0.1, 0.5):
            self._attr_suggested_display_precision = 1
        elif reg.scale == 0.01:
            self._attr_suggested_display_precision = 2
        blob = f"{reg.name} {reg.desc}".lower()
        if any(k in blob for k in _DIAGNOSTIC_KEYWORDS):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        return self.decoded_value()
