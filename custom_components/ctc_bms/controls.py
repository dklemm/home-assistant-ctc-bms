"""The 1000-range control registers, written by hand.

A different family from the BMS map in registers.py, and the one the manual
points at for anything that must change often:

    "There are a number of 1000 registers. These registers are to be used to
    actively control and regulate parameters. These records need to be updated
    every 5 minutes or they will be reset. They will also be reset on restart.
    These parameters can be set as much as the programmer wants, without any
    risks."

So: **write-only, no read-back, no write-cycle cost, and self-cancelling.**
That last property is the safety feature, not an obstacle - a control the
integration stops refreshing is undone by the controller within five minutes,
which is why hold.py refreshes rather than latches, and why nothing is
re-asserted at startup.

Hand-authored, for the same reason groups.py, models.py and names.py are.
parse_bms.py bounds its rows to 60000-62999 and reads pages 23-45; this table
is on page 22 with a *different column set* - no Signed, Max, Min, Step, Bit
or, crucially, **Factor**. Teaching the parser a second row shape for 36 rows
would buy nothing, and dev/bms_registers.json is rewritten wholesale on the
next parse.

## Scales are inferred, and the inference is pinned

Because the manual gives no factor for any of these, every numeric control
borrows its scale from the stored parameter it shadows: 1002 Maximum RPS from
61572 RPSMax (0.1 rps), 1033 Setpoint DHW tank from 62001 sStopTempDHW
(0.1 °C), and so on. `scale_from` records which one, and
tests/test_controls.py::test_inferred_scales_match_their_sibling asserts the
two still agree - so a regenerated map that changes a sibling's factor fails
loudly instead of silently mis-scaling a command to a live heating system.

`dev/ctc_modbus_test.py probe 1002 400 800 --yes` is how to settle any one of
them on real hardware: a read-back that copies the written word exactly proves
the shared scale, and the tool says so.

## Releasing is the absence of a write

Writing 0 is a *command*, not a release: the manual documents 0 = Economy on
1007 and 0 = Off on 1015-1019. So a released control is one nobody is
refreshing, and the controller's own timer undoes it.

For the numeric controls, RELEASE_VALUE (0) is what the UI offers, because 0 is
either not a setting the controller can hold (a DHW tank at 0 °C, a compressor
capped at 0 rps) or is exactly equivalent to released (a curve adjustment of
0 °C). Either way nothing is sent. Selects get an explicit "Not controlled"
option instead, so 1007 can keep its documented Economy.

1100 is the one control written to release, because 0 there means "all 8 bits
open" - the documented resting state - and leaving a SmartGrid block asserted
for up to five minutes after the user cleared it is worse than one free write.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .registers import MAX_ZONES

# What a select shows when nothing is being asserted. Not a register value:
# choosing it stops the refresh and writes nothing.
NOT_CONTROLLED = "Not controlled"

# What a control number is set to in order to release it. See the module
# docstring - this is never written.
RELEASE_VALUE = 0.0

# Register 1100 packs eight virtual digital inputs into one word.
VDI_REGISTER = 1100
VDI_BITS = range(8)


@dataclass(frozen=True)
class Control:
    """One control register, or - for kind "bitfield" - one word of them."""

    number: int
    name: str
    device: str  # "System" | a groups.SUBSYSTEMS key | "Zone1".."Zone4"
    kind: str  # "number" | "select" | "switch" | "bitfield"
    scale: float = 1.0
    unit: str = ""
    # Numbers: (min, max, step) in engineering units. Conservative and
    # hand-written, exactly like SETPOINT_SYSTEM in const.py, and never wider
    # than the sibling stored parameter's documented range. 0 always falls
    # inside, because 0 is how the UI releases.
    limits: tuple[float, float, float] | None = None
    # Selects: {raw value: option}. Only legends the manual spells out in full
    # - the controller accepts undocumented values silently.
    options: dict[int, str] | None = None
    # Switches: the raw value "on" asserts. There is no off value - a control
    # switch turns off by *releasing*, because "stop commanding a start" is
    # what a user means by it and it is the only released state a two-position
    # entity can reach.
    on_value: int = 1
    # The register this control's scale was borrowed from; None where the scale
    # is 1 by inspection, or where there is no sibling to borrow from.
    scale_from: int | None = None
    zone: int | None = None
    requires_model: str | None = None
    enabled_default: bool = True


_ZONE_MODE = {0: "Off", 1: "Heat", 2: "Cool", 3: "Auto", 4: "On"}

# Per-zone families. The manual numbers them contiguously, so member n is at
# base + (n - 1) - the same shape ZONE_FIELDS uses in registers.py, one stride
# smaller.
_ZONE_FAMILIES: tuple[Control, ...] = (
    Control(
        1010,
        "Room temperature setpoint override",
        "Zone",
        "number",
        0.1,
        "°C",
        limits=(0.0, 30.0, 0.5),
        scale_from=61509,  # Zone SetPoint
    ),
    Control(
        1015,
        "Mode override",
        "Zone",
        "select",
        options=_ZONE_MODE,
    ),
    Control(
        1023,
        "Curve adjustment override",
        "Zone",
        "number",
        0.1,
        "°C",
        # Negative is meaningful here, and 0 is genuinely equivalent to
        # released - no adjustment either way - so it can stay the release.
        limits=(-10.0, 10.0, 0.1),
        scale_from=61517,  # Zone Adjustment
    ),
    Control(
        1029,
        "Primary flow setpoint override",
        "Zone",
        "number",
        0.1,
        "°C",
        limits=(0.0, 70.0, 0.5),
        scale_from=62007,  # Zone Temp - setpoint primary flow
    ),
)

_SYSTEM_CONTROLS: tuple[Control, ...] = (
    # 1000/1001 are EcoLogic S only - that controller is for customers running
    # their own logic over shunts and valves, so the BMS starts and stops the
    # compressor itself.
    Control(
        1000,
        "Start heat pump override",
        "System",
        "switch",
        requires_model="ecologic_s",
        # The one row whose value set is inferred rather than documented: the
        # manual gives "Start heatpump" and no legend, and the house rule is
        # that only a fully documented legend may become a switch. Shipped
        # disabled - the same idiom overrides.py uses for the 3-phase currents
        # - and promotable once an EcoLogic S owner probes it.
        enabled_default=False,
    ),
    Control(
        1001,
        "Primary flow setpoint override",
        "System",
        "number",
        0.1,
        "°C",
        limits=(0.0, 70.0, 0.5),
        scale_from=62011,  # Zone PrimaryFlow
        requires_model="ecologic_s",
    ),
    Control(
        1002,
        "Max RPS override",
        "System",
        "number",
        0.1,
        "rps",
        limits=(0.0, 120.0, 1.0),
        scale_from=61572,  # HP RPSMax
    ),
    Control(
        1005,
        "Electricity price mode override",
        "System",
        "select",
        options={1: "Low", 2: "Normal", 3: "High"},
    ),
    Control(
        VDI_REGISTER,
        "Virtual digital inputs",
        "System",
        "bitfield",
    ),
    # -- Hot water -------------------------------------------------------
    Control(
        1006,
        "Extra timer override",
        "DHW",
        "number",
        0.5,
        "h",
        limits=(0.0, 24.0, 0.5),
        scale_from=61503,  # sDHWTimerExtra
    ),
    Control(
        1007,
        "Mode override",
        "DHW",
        "select",
        # The manual's own typo ("Ecomony") is fixed here, as it is for 61500.
        options={0: "Economy", 1: "Normal", 2: "Comfort"},
    ),
    Control(
        1033,
        "Tank setpoint override",
        "DHW",
        "number",
        0.1,
        "°C",
        limits=(0.0, 65.0, 0.5),
        scale_from=62001,  # sStopTempDHW
    ),
    # -- Additional heat -------------------------------------------------
    Control(
        1003,
        "Max power upper tank override",
        "AddHeat",
        "number",
        0.1,
        "kW",
        limits=(0.0, 15.0, 0.1),
        scale_from=61591,  # elhDHWMaxPower
    ),
    Control(
        1004,
        "Max power lower tank override",
        "AddHeat",
        "number",
        0.1,
        "kW",
        limits=(0.0, 15.0, 0.1),
        scale_from=61590,  # elhMaxPower
    ),
    Control(
        1028,
        "Buffer tank setpoint override",
        "AddHeat",
        "number",
        0.1,
        "°C",
        limits=(0.0, 80.0, 0.5),
        scale_from=62250,  # exbUpperTemp
    ),
    # -- Ventilation -----------------------------------------------------
    Control(
        1008,
        "CO2 start setpoint override",
        "Ventilation",
        "number",
        1.0,
        "ppm",
        limits=(0.0, 2000.0, 10.0),
        scale_from=62281,  # sHighestCO2
    ),
    Control(
        1009,
        "Humidity start setpoint override",
        "Ventilation",
        "number",
        1.0,
        "%",
        limits=(0.0, 100.0, 1.0),
        scale_from=62282,  # sHighestRH
    ),
    Control(
        1021,
        "Boost override",
        "Ventilation",
        "number",
        1.0,
        "",
        # No sibling and no legend: the manual gives "Boost ventilation" and
        # nothing else, so both the unit and the range are a guess. Kept narrow
        # deliberately - a wrong guess should not be able to command something
        # extreme. `probe 1021 1 2 --yes` is what settles it.
        limits=(0.0, 240.0, 1.0),
    ),
    Control(
        1022,
        "Mode override",
        "Ventilation",
        "select",
        options={-1: "Reduced", 0: "Normal", 1: "Forced"},
    ),
    # -- Cooling ---------------------------------------------------------
    Control(
        1014,
        "Setpoint override",
        "Cooling",
        "number",
        0.1,
        "°C",
        limits=(0.0, 30.0, 0.5),
        scale_from=61659,  # room_temp_cooling
    ),
    Control(
        1019,
        "Mode override",
        "Cooling",
        "select",
        options=_ZONE_MODE,
    ),
    Control(
        1027,
        "Setpoint offset override",
        "Cooling",
        "number",
        0.1,
        "°C",
        limits=(-10.0, 10.0, 0.1),
        scale_from=61517,  # Zone Adjustment - the heating offsets' sibling
    ),
    # -- Pool ------------------------------------------------------------
    Control(
        1020,
        "Setpoint override",
        "Pool",
        "number",
        0.1,
        "°C",
        limits=(0.0, 40.0, 0.5),
        scale_from=61531,  # poolTempStopSetting
    ),
)

CONTROLS: tuple[Control, ...] = _SYSTEM_CONTROLS + tuple(
    replace(
        family,
        number=family.number + (n - 1),
        device=f"Zone{n}",
        zone=n,
    )
    for family in _ZONE_FAMILIES
    for n in range(1, MAX_ZONES + 1)
)


def to_word(control: Control, value: float) -> int:
    """Engineering value -> the raw word to write.

    Masked to 16 bits because 1022 documents -1 = Reduced; every other control
    is non-negative.
    """
    return round(value / control.scale) & 0xFFFF
