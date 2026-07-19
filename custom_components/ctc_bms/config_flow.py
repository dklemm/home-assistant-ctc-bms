"""Config and options flow for the CTC BMS integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DEVICE_ID,
    CONF_HEAT_PUMPS,
    CONF_SETPOINTS,
    CONF_ZONES,
    DEFAULT_DEVICE_ID,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .decode import is_sentinel
from .hub import CtcConnectionError, CtcHub
from .registers import (
    MAX_HEAT_PUMPS,
    MAX_ZONES,
    Reg,
    registers_for_hp,
    registers_for_zone,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID): int,
    }
)


def _is_present(regs: list[Reg], words: dict[int, int]) -> bool:
    """Whether this HP/zone is physically fitted.

    Every HP/Zone register responds whether or not the hardware exists (absent
    hardware reads 0), so presence is decided by *real nonzero data* - and the
    -9999/-10000 no-sensor sentinel is evidence of absence, not data. Requiring
    two data registers keeps a single stray value from creating a device.
    """
    data = 0
    for reg in regs:
        if reg.access != "R":
            continue
        got = [words.get(reg.number + i) for i in range(reg.count)]
        if any(w is None for w in got):
            continue
        if is_sentinel(reg, got):
            continue
        if any(got):
            data += 1
    return data >= 2


async def _probe_and_detect(
    host: str, port: int, device_id: int
) -> tuple[list[int], list[int]]:
    """Connect, verify the controller answers, and detect fitted hardware."""
    hub = CtcHub(host, port, device_id)
    try:
        await hub.async_connect()
        await hub.async_probe()
        wanted: set[int] = set()
        for n in range(1, MAX_HEAT_PUMPS + 1):
            for reg in registers_for_hp(n):
                if reg.access == "R":
                    wanted.update(range(reg.number, reg.number + reg.count))
        for n in range(1, MAX_ZONES + 1):
            for reg in registers_for_zone(n):
                if reg.access == "R":
                    wanted.update(range(reg.number, reg.number + reg.count))
        words = await hub.async_read_addresses(wanted)
    finally:
        hub.close()

    heat_pumps = [
        n
        for n in range(1, MAX_HEAT_PUMPS + 1)
        if _is_present(registers_for_hp(n), words)
    ]
    zones = [
        n
        for n in range(1, MAX_ZONES + 1)
        if _is_present(registers_for_zone(n), words)
    ]
    return heat_pumps, zones


class CtcBmsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            device_id = user_input[CONF_DEVICE_ID]
            await self.async_set_unique_id(f"{host}:{port}:{device_id}")
            self._abort_if_unique_id_configured()
            try:
                heat_pumps, zones = await _probe_and_detect(
                    host, port, device_id
                )
            except CtcConnectionError:
                errors["base"] = "cannot_connect"
            else:
                _LOGGER.info(
                    "Detected heat pumps %s and heating systems %s",
                    heat_pumps,
                    zones,
                )
                return self.async_create_entry(
                    title=f"CTC ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_DEVICE_ID: device_id,
                        # An idle system (summer, compressor off) can read 0
                        # everywhere and hide real hardware; guarantee at least
                        # HP1/Zone1, and let options override the rest.
                        CONF_HEAT_PUMPS: heat_pumps or [1],
                        CONF_ZONES: zones or [1],
                    },
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CtcOptionsFlow:
        return CtcOptionsFlow()


class CtcOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        entry = self.config_entry

        def current(key: str, default):
            if key in entry.options:
                return entry.options[key]
            return entry.data.get(key, default)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(
                    CONF_HEAT_PUMPS,
                    default=[str(n) for n in current(CONF_HEAT_PUMPS, [1])],
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            str(n) for n in range(1, MAX_HEAT_PUMPS + 1)
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_ZONES,
                    default=[str(n) for n in current(CONF_ZONES, [1])],
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[str(n) for n in range(1, MAX_ZONES + 1)],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_SETPOINTS, default=current(CONF_SETPOINTS, True)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
