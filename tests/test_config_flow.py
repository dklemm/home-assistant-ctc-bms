"""Config flow: happy path, connection failure, duplicates, detection rule."""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ctc_bms.config_flow import _is_present
from custom_components.ctc_bms.const import (
    CONF_DEVICE_ID,
    CONF_HEAT_PUMPS,
    CONF_ZONES,
    DOMAIN,
)
from custom_components.ctc_bms.hub import CtcConnectionError
from custom_components.ctc_bms.registers import registers_for_hp

USER_INPUT = {CONF_HOST: "1.2.3.4", CONF_PORT: 502, CONF_DEVICE_ID: 1}


async def test_user_flow_creates_entry(hass):
    with (
        patch(
            "custom_components.ctc_bms.config_flow._probe_and_detect",
            return_value=([1], [1, 2]),
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
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "CTC (1.2.3.4)"
    assert result["data"][CONF_HEAT_PUMPS] == [1]
    assert result["data"][CONF_ZONES] == [1, 2]


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
    assert not _is_present(regs, {r.number: 0 for r in regs})

    # sentinels are evidence of ABSENCE, not data
    words = {r.number: 0 for r in regs}
    words[addr["TempIn"]] = 55536
    words[addr["TempOut"]] = 55537
    assert not _is_present(regs, words)

    # one real reading is "maybe" - still not enough
    words = {r.number: 0 for r in regs}
    words[addr["TempIn"]] = 466
    assert not _is_present(regs, words)

    # two real readings -> present
    words[addr["Status"]] = 3
    assert _is_present(regs, words)
