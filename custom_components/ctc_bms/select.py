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

from .controls import NOT_CONTROLLED, VDI_REGISTER, Control, to_word
from .coordinator import CtcConfigEntry, CtcCoordinator
from .decode import decode_int16, encode_value
from .entity import CtcControlEntity, CtcEntity
from .registers import Reg

# The manual's SmartGrid truth table, keyed by the labels ENUM_SYSTEM uses for
# the 62301 SGMode read-back so the command and the reading agree word for
# word. Values are (A closed, B closed) on whichever two digital inputs the
# controller's menus assign - which is why the bits are configuration.
SMARTGRID_STATES: dict[str, tuple[int, int]] = {
    "None/Normal": (0, 0),
    "Block": (1, 0),
    "Low price": (0, 1),
    "High capacity": (1, 1),
}


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
    async_add_entities(
        CtcControlSelect(coordinator, control)
        for control in coordinator.controls()
        if control.kind == "select"
    )
    if coordinator.smartgrid_bits:
        vdi = next(
            (c for c in coordinator.controls() if c.number == VDI_REGISTER),
            None,
        )
        if vdi is not None:
            async_add_entities([CtcSmartGridSelect(coordinator, vdi)])


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


class CtcControlSelect(CtcControlEntity, SelectEntity):
    """A 1000-range mode, asserted for as long as it is held.

    "Not controlled" leads the options and is not a register value: choosing it
    stops the refresh and writes nothing. It has to be a separate option rather
    than a value, because the manual documents 0 as Economy on 1007 and Off on
    the zone modes - writing 0 there is a command, and on a zone mode it is the
    command that turns the heating off.
    """

    def __init__(self, coordinator: CtcCoordinator, control: Control) -> None:
        super().__init__(coordinator, control)
        self._options = control.options
        self._values = {option: value for value, option in control.options.items()}
        self._attr_options = [NOT_CONTROLLED, *control.options.values()]

    @property
    def current_option(self) -> str | None:
        word = self.held_word()
        if word is None:
            return NOT_CONTROLLED
        # 1022 documents -1 = Reduced; every other control is non-negative.
        return self._options.get(decode_int16([word]))

    async def async_select_option(self, option: str) -> None:
        if option == NOT_CONTROLLED:
            await self.hold.async_release(self.control.number)
            return
        await self.hold.async_set(
            self.control.number, to_word(self.control, self._values[option])
        )


class CtcSmartGridSelect(CtcControlEntity, SelectEntity):
    """SmartGrid, as two bits of the virtual-digital-input word.

    Only exists once the options flow says which inputs carry SmartGrid A and
    B: the manual is explicit that a terminal's DI number is configured in the
    controller's own menus, so it cannot be looked up - only told to us, or
    found with `dev/ctc_modbus_test.py discover-di`.

    No "Not controlled" option, because this one has a documented resting
    state: both inputs open is "None/Normal", which is what the controller does
    with no BMS attached. Reading it while nothing is held says the same thing,
    honestly.
    """

    _attr_options = list(SMARTGRID_STATES)

    def __init__(self, coordinator: CtcCoordinator, control: Control) -> None:
        super().__init__(
            coordinator, control, name="SmartGrid", unique_suffix="smartgrid"
        )
        self._a, self._b = coordinator.smartgrid_bits

    @property
    def current_option(self) -> str:
        word = self.held_word() or 0
        closed = ((word >> self._a) & 1, (word >> self._b) & 1)
        return next(
            option
            for option, bits in SMARTGRID_STATES.items()
            if bits == closed
        )

    async def async_select_option(self, option: str) -> None:
        a_closed, b_closed = SMARTGRID_STATES[option]
        # Read-modify-write the one held word: the other six inputs may be
        # asserted too, and two entities must never fight over 1100.
        word = self.held_word() or 0
        word = (word & ~(1 << self._a)) | (a_closed << self._a)
        word = (word & ~(1 << self._b)) | (b_closed << self._b)
        if word == 0:
            await self.hold.async_release(VDI_REGISTER)
            return
        await self.hold.async_set(VDI_REGISTER, word)
