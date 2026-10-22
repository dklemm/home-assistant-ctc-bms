"""Sensors for every readable CTC BMS register."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import CtcConfigEntry, CtcCoordinator
from .entity import CtcEntity
from .heat import heat_output_kw
from .overrides import override_for
from .registers import Reg, registers_for_hp

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
    "W": (
        UnitOfPower.WATT,
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
    ),
    "A": (
        UnitOfElectricCurrent.AMPERE,
        SensorDeviceClass.CURRENT,
        SensorStateClass.MEASUREMENT,
    ),
    "kWh": (
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL_INCREASING,
    ),
    "l/min": (
        UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        SensorDeviceClass.VOLUME_FLOW_RATE,
        SensorStateClass.MEASUREMENT,
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
        if coordinator.platform_for(device_key, reg) == "sensor"
    )
    async_add_entities(
        sensor
        for n in coordinator.heat_pumps
        for sensor in (
            CtcHeatOutputSensor(coordinator, n),
            CtcHeatEnergySensor(coordinator, n),
        )
    )


class CtcSensor(CtcEntity, SensorEntity):
    def __init__(
        self, coordinator: CtcCoordinator, device_key: str, reg: Reg
    ) -> None:
        super().__init__(coordinator, device_key, reg)
        override = override_for(reg.number)
        self._factor = override.factor
        self._attr_entity_registry_enabled_default = override.enabled_default
        self._options = coordinator.enum_options(reg)
        if self._options is not None:
            # An enum sensor reports a state, not a measurement: HA requires no
            # unit and no state class, and long-term statistics make no sense
            # for one anyway.
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(self._options.values())
            self._set_entity_category(reg)
            return
        effective_unit = override.unit if override.unit is not None else reg.unit
        unit, device_class, state_class = _UNIT_MAP.get(
            effective_unit, (effective_unit or None, None, None)
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
        self._set_entity_category(reg)

    def _set_entity_category(self, reg: Reg) -> None:
        blob = f"{reg.name} {reg.desc}".lower()
        if any(k in blob for k in _DIAGNOSTIC_KEYWORDS):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | str | None:
        value = self.decoded_value()
        if value is None:
            return None
        if self._options is not None:
            # Outside the documented legend: unknown beats an invented state,
            # which HA would reject as not one of `options` anyway.
            return self._options.get(int(value))
        return round(value * self._factor, 4) if self._factor != 1.0 else value


class CtcHeatOutputSensor(CoordinatorEntity[CtcCoordinator], SensorEntity):
    """Heat one heat pump is delivering, derived from flow and ΔT.

    Beside CtcSensor rather than under it: there is no register behind this,
    so the unique_id is a name rather than a register number.
    """

    _attr_has_entity_name = True
    _attr_name = "Heat output"
    _key = "heat_output"
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: CtcCoordinator, n: int) -> None:
        super().__init__(coordinator)
        regs = {reg.name: reg for reg in registers_for_hp(n)}
        self._flow = regs[f"HP{n} PrimarySystemFlow"]
        self._t_in = regs[f"HP{n} TempIn"]
        self._t_out = regs[f"HP{n} TempOut"]
        self._rps = regs[f"HP{n} CurrRPS"]
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_hp{n}_{self._key}"
        self._attr_device_info = coordinator.device_info(f"HP{n}")

    def heat_output(self) -> float | None:
        decoded = self.coordinator.decoded
        return heat_output_kw(
            decoded(self._flow),
            decoded(self._t_in),
            decoded(self._t_out),
            decoded(self._rps),
        )

    @property
    def native_value(self) -> float | None:
        return self.heat_output()


class CtcHeatEnergySensor(CtcHeatOutputSensor, RestoreSensor):
    """Heat delivered in total: the heat output summed over the polls.

    Each poll's output is held until the next one (a left Riemann sum), which
    is all a sampled reading can honestly claim. Negative output - a defrost -
    is not subtracted, so the total only rises and the Energy dashboard can use
    it.
    """

    _attr_name = "Heat energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _key = "heat_energy"

    def __init__(self, coordinator: CtcCoordinator, n: int) -> None:
        super().__init__(coordinator, n)
        self._total = 0.0
        self._last: tuple[datetime, float] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            if last.native_value is not None:
                self._total = float(last.native_value)
        # Setup has already polled once; start summing from that poll.
        self._sample(dt_util.utcnow())

    def _sample(self, now: datetime) -> None:
        kw = self.heat_output() if self.coordinator.last_update_success else None
        self._last = None if kw is None else (now, kw)

    def _handle_coordinator_update(self) -> None:
        now = dt_util.utcnow()
        if self._last is not None:
            then, last_kw = self._last
            # Over a gap - an outage, a restart, a skipped poll - we don't
            # know what the pump did, and inventing heat is worse than
            # missing some.
            if now - then <= 2 * self.coordinator.update_interval:
                hours = (now - then).total_seconds() / 3600
                self._total += max(last_kw, 0.0) * hours
        self._sample(now)
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._total, 3)
