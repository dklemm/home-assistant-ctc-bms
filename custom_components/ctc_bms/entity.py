"""Base entity for CTC BMS registers."""

from __future__ import annotations

import re

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import CtcCoordinator
from .decode import decode_value, is_sentinel
from .registers import Reg


def register_entity_name(device_key: str, reg: Reg) -> str:
    """A human name for a register, without the device prefix.

    'HP1 DischargeGas' -> 'Discharge gas'; 'sOutsideTemp' -> 'Outside temp';
    'hc1_heating_curve_point_1_x_value_outsid' -> 'Hc1 heating curve point ...'.
    The device provides context (has_entity_name), so the HP1/Zone1 prefix goes.
    """
    name = reg.name
    if device_key != "System" and " " in name:
        name = name.split(" ", 1)[1]
    if name.startswith("s") and len(name) > 1 and name[1].isupper():
        name = name[1:]
    if "_" in name:
        words = name.split("_")
    else:
        words = re.findall(r"[A-Z]+(?=[A-Z][a-z0-9]|\b)|[A-Z]?[a-z0-9]+", name)
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
