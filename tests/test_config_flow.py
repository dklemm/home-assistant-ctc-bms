"""Config flow: happy path, connection failure, duplicates, detection rule."""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ctc_bms.decode import is_present
from custom_components.ctc_bms.groups import SUBSYSTEMS
from custom_components.ctc_bms.models import DEFAULT_MODEL
from custom_components.ctc_bms.const import (
    CONF_DEVICE_ID,
    CONF_HEAT_PUMPS,
    CONF_MODEL,
    CONF_SETPOINTS,
    CONF_SUBSYSTEMS,
    CONF_ZONES,
    DOMAIN,
)
from custom_components.ctc_bms.hub import CtcConnectionError
from custom_components.ctc_bms.registers import registers_for_hp

USER_INPUT = {CONF_HOST: "1.2.3.4", CONF_PORT: 502, CONF_DEVICE_ID: 1}


async def test_user_flow_creates_entry(hass):
    """Connect, then confirm the model - product type 14 is an EcoLogic M."""
    with (
        patch(
            "custom_components.ctc_bms.config_flow._probe_and_detect",
            return_value=([1], [1, 2], 14),
        ),
        patch(
            "custom_components.ctc_bms.async_setup_entry", return_value=True
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        # Second step: the model, recognised from sProductType.
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "model"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MODEL: "ecologic_m", CONF_SUBSYSTEMS: ["DHW", "AddHeat"]},
        )
        # Third step: opting in to the writable entities.
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "setpoints"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SETPOINTS: True}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "CTC (1.2.3.4)"
    assert result["data"][CONF_HEAT_PUMPS] == [1]
    assert result["data"][CONF_ZONES] == [1, 2]
    assert result["data"][CONF_MODEL] == "ecologic_m"
    assert result["data"][CONF_SUBSYSTEMS] == ["DHW", "AddHeat"]
    assert result["data"][CONF_SETPOINTS] is True


async def test_writable_entities_are_off_by_default(hass):
    """Writes wear the controller's stored parameters out, so opting in is
    deliberate - and the choice is stored, not left to fall back."""
    with (
        patch(
            "custom_components.ctc_bms.config_flow._probe_and_detect",
            return_value=([1], [1], 14),
        ),
        patch(
            "custom_components.ctc_bms.async_setup_entry", return_value=True
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MODEL: "ecologic_m", CONF_SUBSYSTEMS: ["DHW"]},
        )
        assert result["step_id"] == "setpoints"
        defaults = {
            key.schema: key.default() for key in result["data_schema"].schema
        }
        assert defaults[CONF_SETPOINTS] is False

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SETPOINTS: False}
        )
    assert result["data"][CONF_SETPOINTS] is False


async def test_model_step_defaults_come_from_product_type(hass):
    """An unrecognised product type falls back to 'other': everything ticked."""
    with patch(
        "custom_components.ctc_bms.config_flow._probe_and_detect",
        return_value=([1], [1], 999),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["step_id"] == "model"
    defaults = {
        key.schema: key.default() for key in result["data_schema"].schema
    }
    assert defaults[CONF_MODEL] == DEFAULT_MODEL
    assert defaults[CONF_SUBSYSTEMS] == list(SUBSYSTEMS)


async def test_user_flow_cannot_connect(hass):
    with patch(
        "custom_components.ctc_bms.config_flow._probe_and_detect",
        side_effect=CtcConnectionError("silence"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_aborts(hass):
    MockConfigEntry(
        domain=DOMAIN, data=USER_INPUT, unique_id="1.2.3.4:502:1"
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def test_presence_rule():
    regs = registers_for_hp(1)
    addr = {r.name.split(" ", 1)[1]: r.number for r in regs}

    # all zero -> absent hardware
    assert not is_present(regs, {r.number: 0 for r in regs})

    # sentinels are evidence of ABSENCE, not data
    words = {r.number: 0 for r in regs}
    words[addr["TempIn"]] = 55536
    words[addr["TempOut"]] = 55537
    assert not is_present(regs, words)

    # one real reading is "maybe" - still not enough
    words = {r.number: 0 for r in regs}
    words[addr["TempIn"]] = 466
    assert not is_present(regs, words)

    # two real readings -> present
    words[addr["Status"]] = 3
    assert is_present(regs, words)
