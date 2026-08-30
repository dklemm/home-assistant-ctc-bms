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

from .controls import RELEASE_VALUE, Control, to_word
from .coordinator import CtcConfigEntry, CtcCoordinator
from .decode import decode_int16, encode_value
from .entity import CtcControlEntity, CtcEntity
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
    async_add_entities(
        CtcControlNumber(coordinator, control)
        for control in coordinator.controls()
        if control.kind == "number"
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


class CtcControlNumber(CtcControlEntity, NumberEntity):
    """A 1000-range setpoint, asserted for as long as it is held.

    Unknown means released - the controller is using its own setting - and
    setting the number to 0 goes back there. 0 is safe to overload because it
    is never a setting these registers can hold (a DHW tank at 0 °C, a
    compressor capped at 0 rps) or, for the curve adjustments, is exactly what
    released means anyway. Nothing is written either way: releasing is letting
    the controller's five-minute timer run out.
    """

    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: CtcCoordinator, control: Control) -> None:
        super().__init__(coordinator, control)
        low, high, step = control.limits
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        if control.unit == "°C":
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif control.unit:
            self._attr_native_unit_of_measurement = control.unit

    @property
    def native_value(self) -> float | None:
        word = self.held_word()
        if word is None:
            return None
        return round(decode_int16([word]) * self.control.scale, 4)

    async def async_set_native_value(self, value: float) -> None:
        if value == RELEASE_VALUE:
            await self.hold.async_release(self.control.number)
            return
        await self.hold.async_set(
            self.control.number, to_word(self.control, value)
        )
