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
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CONTROLS,
    CONF_DEVICE_ID,
    CONF_HEAT_PUMPS,
    CONF_MODEL,
    CONF_SETPOINTS,
    CONF_SMARTGRID_A,
    CONF_SMARTGRID_B,
    CONF_SUBSYSTEMS,
    CONF_ZONES,
    REG_PRODUCT_TYPE,
    DEFAULT_DEVICE_ID,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SMARTGRID_UNUSED,
)
from .controls import VDI_BITS
from .decode import is_present
from .groups import SUBSYSTEMS
from .hub import CtcConnectionError, CtcHub
from .models import DEFAULT_MODEL, MODELS, model_for_product_type
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


async def _probe_and_detect(
    host: str, port: int, device_id: int
) -> tuple[list[int], list[int], int | None]:
    """Connect, verify the controller answers, and detect fitted hardware.

    Heat pumps and zones are detected; subsystems deliberately are not. A
    controller answers every subsystem register whether or not the hardware
    exists - an EcoLogic M with no solar still reports sunPump at 100% - so
    they are chosen from the model instead (see models.py).
    """
    hub = CtcHub(host, port, device_id)
    try:
        await hub.async_probe()
        wanted: set[int] = set()

        def want(regs: list[Reg]) -> None:
            for reg in regs:
                if reg.access == "R":
                    wanted.update(range(reg.number, reg.number + reg.count))

        for n in range(1, MAX_HEAT_PUMPS + 1):
            want(registers_for_hp(n))
        for n in range(1, MAX_ZONES + 1):
            want(registers_for_zone(n))
        wanted.add(REG_PRODUCT_TYPE)
        words = await hub.async_read_addresses(wanted)
    finally:
        await hub.async_close()

    heat_pumps = [
        n
        for n in range(1, MAX_HEAT_PUMPS + 1)
        if is_present(registers_for_hp(n), words)
    ]
    zones = [
        n
        for n in range(1, MAX_ZONES + 1)
        if is_present(registers_for_zone(n), words)
    ]
    return heat_pumps, zones, words.get(REG_PRODUCT_TYPE)


class CtcBmsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._detected: dict[str, Any] = {}
        self._model: str = DEFAULT_MODEL

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
                heat_pumps, zones, product_type = await _probe_and_detect(
                    host, port, device_id
                )
            except CtcConnectionError:
                errors["base"] = "cannot_connect"
            else:
                _LOGGER.info(
                    "Detected heat pumps %s, heating systems %s, "
                    "product type %s",
                    heat_pumps,
                    zones,
                    product_type,
                )
                self._detected = {
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_DEVICE_ID: device_id,
                    # An idle system (summer, compressor off) can read 0
                    # everywhere and hide real hardware; guarantee at least
                    # HP1/Zone1, and let options override the rest.
                    CONF_HEAT_PUMPS: heat_pumps or [1],
                    CONF_ZONES: zones or [1],
                }
                self._model = model_for_product_type(product_type)
                return await self.async_step_model()
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the controller model, which decides the subsystems.

        Pre-filled from sProductType when that value is recognised; the model
        only seeds the checkboxes, so anything optional the install actually
        has can be ticked here or later in the options.
        """
        if user_input is not None:
            self._detected.update(user_input)
            return await self.async_step_setpoints()

        schema = vol.Schema(
            {
                vol.Required(CONF_MODEL, default=self._model): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=key, label=model.name)
                            for key, model in MODELS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SUBSYSTEMS,
                    default=list(MODELS[self._model].subsystems),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=key, label=name)
                            for key, name in SUBSYSTEMS.items()
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="model", data_schema=schema)

    async def async_step_setpoints(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Opt in to the writable entities, which default to off.

        A step of its own rather than one more checkbox on the model step,
        because the warning is the point: the registers behind these entities
        are the controller's stored parameters and the manual limits how many
        times they may be written (see the banner in const.py). Off by default
        means an install has to make that choice deliberately.

        The value is stored explicitly, so it is only new entries that start
        read-only - an entry with no stored value keeps its writable entities,
        the same rule the subsystem list follows.
        """
        if user_input is not None:
            self._detected.update(user_input)
            return await self.async_step_controls()

        schema = vol.Schema(
            {vol.Required(CONF_SETPOINTS, default=False): BooleanSelector()}
        )
        return self.async_show_form(step_id="setpoints", data_schema=schema)

    async def async_step_controls(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Opt in to the 1000-range control entities, which default to off.

        A separate question from the setpoints, because it is a different risk:
        these registers cost no write cycles, but holding one means the
        integration writes to a live heating system every minute of its own
        accord - the only thing it ever does without a service call behind it.

        Off by default and, unlike CONF_SETPOINTS, off as the *fallback* too
        (see the note in coordinator.py): no config entry predates this step,
        so there is nothing an upgrade could take away.
        """
        if user_input is not None:
            return self.async_create_entry(
                title=f"CTC ({self._detected[CONF_HOST]})",
                data={**self._detected, **user_input},
            )

        schema = vol.Schema(
            {vol.Required(CONF_CONTROLS, default=False): BooleanSelector()}
        )
        return self.async_show_form(step_id="controls", data_schema=schema)

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
                    CONF_MODEL, default=current(CONF_MODEL, DEFAULT_MODEL)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=key, label=model.name)
                            for key, model in MODELS.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                # Untick a subsystem you don't have: its device and entities
                # disappear and its registers drop out of the poll. The model
                # only seeded these at setup; from here they are yours.
                vol.Required(
                    CONF_SUBSYSTEMS,
                    default=list(current(CONF_SUBSYSTEMS, list(SUBSYSTEMS))),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=key, label=name)
                            for key, name in SUBSYSTEMS.items()
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                # Same fallback as the coordinator: an entry predating the
                # setup step has no stored value and keeps what it has.
                vol.Required(
                    CONF_SETPOINTS, default=current(CONF_SETPOINTS, True)
                ): BooleanSelector(),
                # And False here for the same reason it is False in the
                # coordinator: no entry predates the controls step, so there
                # are no entities an upgrade could remove. The two defaults
                # answer different questions - don't reconcile them.
                vol.Required(
                    CONF_CONTROLS, default=current(CONF_CONTROLS, False)
                ): BooleanSelector(),
                vol.Required(
                    CONF_SMARTGRID_A,
                    default=current(CONF_SMARTGRID_A, SMARTGRID_UNUSED),
                ): _di_selector(),
                vol.Required(
                    CONF_SMARTGRID_B,
                    default=current(CONF_SMARTGRID_B, SMARTGRID_UNUSED),
                ): _di_selector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _di_selector() -> SelectSelector:
    """Which virtual digital input carries one half of SmartGrid.

    Configuration rather than a constant because the manual is explicit that a
    terminal's DI number is set in the controller's own menus. Leave either at
    "Not used" and no SmartGrid entity is created; the raw input switches are
    always there.
    """
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=SMARTGRID_UNUSED, label="Not used"),
                *(
                    SelectOptionDict(value=str(bit), label=f"DI{bit}")
                    for bit in VDI_BITS
                ),
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )
