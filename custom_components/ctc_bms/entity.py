"""Base entity for CTC BMS registers."""

from __future__ import annotations

import re

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import CtcCoordinator
from .decode import decode_value, is_sentinel
from .hub import CtcConnectionError
from .names import name_for
from .registers import Reg


def register_entity_name(device_key: str, reg: Reg) -> str:
    """A human name for a register, without the device prefix.

    Every register that ships as an entity is named by hand in names.py,
    because the manual's Name column is shorthand and is blank on the rows the
    generator has to slug from the description.

    Deriving one from reg.name is the fallback for a register newly promoted to
    an entity before it has been named: 'HP1 DischargeGas' -> 'Discharge gas',
    'sOutsideTemp' -> 'Outside temp'. The device provides context
    (has_entity_name), so the HP1/Zone1 prefix goes.
    """
    if curated := name_for(reg):
        return curated
    name = reg.name
    # Only the array maps prefix their names ('HP1 DischargeGas'); system
    # registers keep theirs whichever device they end up grouped onto.
    if device_key.startswith(("HP", "Zone")) and " " in name:
        name = name.split(" ", 1)[1]
    if name.startswith("s") and len(name) > 1 and name[1].isupper():
        name = name[1:]
    if "_" in name:
        words = name.split("_")
    else:
        words = re.findall(r"[A-Z]+(?=[A-Z][a-z0-9]|\b)|[A-Z]?[a-z0-9]+", name)
    # Sentence case, per HA's naming convention: lower-case the words that are
    # merely capitalised and leave the rest ('RPS', 'SW', 'O2') alone.
    words = [
        w.lower() if i and len(w) > 1 and w[1:].islower() else w
        for i, w in enumerate(words)
    ]
    pretty = " ".join(words)
    return (pretty[0].upper() + pretty[1:]) if pretty else reg.name


class CtcEntity(CoordinatorEntity[CtcCoordinator]):
    """One register on one HA device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CtcCoordinator, device_key: str, reg: Reg
    ) -> None:
        super().__init__(coordinator)
        self.reg = reg
        # What we last wrote, until a poll supersedes it - see async_write_raw.
        self._written_raw: int | None = None
        self._attr_name = register_entity_name(device_key, reg)
        # Register numbers are the stable identity of this map.
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{reg.number}"
        )
        self._attr_device_info = coordinator.device_info(device_key)

    def _words(self) -> list[int] | None:
        """This register's raw words, or None if absent from the last poll."""
        data = self.coordinator.data or {}
        words = [data.get(self.reg.number + i) for i in range(self.reg.count)]
        if any(w is None for w in words):
            return None
        return words

    @property
    def available(self) -> bool:
        # A register this unit doesn't implement stays unavailable; the
        # -9999/-10000 "no sensor" sentinel is instead surfaced as unknown
        # (None) by value(), because the register itself did respond.
        return super().available and self._words() is not None

    # NB: not named `value` - NumberEntity has a `value` property and shadowing
    # it turns the entity state into a bound method.
    def decoded_value(self) -> float | None:
        words = self._words()
        if words is None or is_sentinel(self.reg, words):
            return None
        return decode_value(self.reg, words)

    async def async_write_raw(self, raw: int) -> None:
        """Write one raw word to this register - unless it already holds it.

        WARNING: the registers behind every writable entity are the
        controller's stored parameters, and the BMS manual is explicit that
        they have a limited number of write cycles:

            "These parameters must not be changed a lot of times. If you do so
            you risk breaking the controller of the heat pump installation.
            There is a limit to the amount of write cycles!"

        Home Assistant does not suppress a service call that matches the
        current state - `switch.turn_on` on an already-on switch still reaches
        the entity - so an automation that re-asserts a value on a timer would
        otherwise burn a write cycle every time it ran, for ever. Hence this
        is the single write path for all three writable platforms: comparing
        raw words (not engineering values) makes it exact, including for a
        number whose requested value rounds to the word already stored.

        What we last wrote wins over the last poll until a poll supersedes it,
        because async_request_refresh is debounced: two writes inside that
        cooldown would otherwise both compare against pre-write data, and a
        write of A, then B, then A again in quick succession would skip the
        last one and leave the register on B.

        Failing that, the comparison is the last poll, so it can be up to one
        scan interval stale: change a setting at the controller's own panel and
        an automation that writes the value HA last saw could be skipped within
        that window. That is the accepted trade-off - the alternative is a read
        before every write, and the window closes at the next poll.

        For anything that genuinely needs to change often, the manual's answer
        is the 1000-range control registers, which are free of write-cycle cost
        (and which this integration does not implement yet).
        """
        current = self._written_raw
        if current is None:
            words = self._words()
            current = None if words is None else words[0]
        if current == raw:
            return
        try:
            await self.coordinator.hub.async_write_register(self.reg.number, raw)
        except CtcConnectionError as err:
            raise HomeAssistantError(
                f"Writing {self.reg.name} failed: {err}"
            ) from err
        self._written_raw = raw
        # Read back so the UI shows what the pump actually accepted (it may
        # clamp the value).
        await self.coordinator.async_request_refresh()

    def _handle_coordinator_update(self) -> None:
        # A completed poll is authoritative again: it reflects our write, or a
        # change made at the controller's own panel.
        self._written_raw = None
        super()._handle_coordinator_update()
