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

from .controls import VDI_BITS, VDI_REGISTER, Control
from .coordinator import CtcConfigEntry, CtcCoordinator
from .decode import encode_value
from .entity import CtcControlEntity, CtcEntity
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
    async_add_entities(
        CtcControlSwitch(coordinator, control)
        for control in coordinator.controls()
        if control.kind == "switch"
    )
    async_add_entities(
        CtcVirtualInputSwitch(coordinator, control, bit)
        for control in coordinator.controls()
        if control.kind == "bitfield"
        for bit in VDI_BITS
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


class CtcControlSwitch(CtcControlEntity, SwitchEntity):
    """A 1000-range control that is a command or nothing.

    Off is *release*, not a value: a two-position entity has no third state,
    and "stop commanding a start" is what turning this off means. So off reads
    as off whether we released it or never held it, which is the same thing to
    the controller five minutes later.
    """

    @property
    def is_on(self) -> bool:
        return self.held_word() == self.control.on_value

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hold.async_set(self.control.number, self.control.on_value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hold.async_release(self.control.number)


class CtcVirtualInputSwitch(CtcControlEntity, SwitchEntity):
    """One bit of register 1100 - a virtual digital input.

    The manual: "If register 1100 is 0 it means that all 8 bits (0 to 7) are
    open." So off is the resting state and clearing the last closed input
    releases the register outright, which is the one control we write to
    release (0 = all open is a documented state, and a SmartGrid block that
    lingers for five minutes after being cleared is worse than a free write).

    All eight of these, and the SmartGrid select, read-modify-write the *same*
    held word, so they cannot fight. Two masters still can: disable the config
    entry before running the CLI's `probe` or `discover-di`.

    Off by default in the registry, because which terminal function each input
    carries is configured in the controller's own menus - most installs use
    none of them, and `discover-di` is how you find out which ones do anything.
    """

    def __init__(
        self, coordinator: CtcCoordinator, control: Control, bit: int
    ) -> None:
        super().__init__(
            coordinator,
            control,
            name=f"Virtual digital input {bit}",
            unique_suffix=f"di{bit}",
        )
        self._attr_entity_registry_enabled_default = False
        self._bit = bit

    @property
    def is_on(self) -> bool:
        return bool((self.held_word() or 0) >> self._bit & 1)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_word((self.held_word() or 0) | 1 << self._bit)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_word((self.held_word() or 0) & ~(1 << self._bit))

    async def _set_word(self, word: int) -> None:
        if word == 0:
            await self.hold.async_release(VDI_REGISTER)
            return
        await self.hold.async_set(VDI_REGISTER, word)
