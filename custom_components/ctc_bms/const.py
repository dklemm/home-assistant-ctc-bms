"""Constants for the CTC BMS integration."""

from __future__ import annotations

DOMAIN = "ctc_bms"

DEFAULT_PORT = 502
DEFAULT_DEVICE_ID = 1  # the controller's "MB Address"
DEFAULT_SCAN_INTERVAL = 30  # a full poll takes ~0.2 s on the hardware
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 300

CONF_DEVICE_ID = "device_id"
CONF_HEAT_PUMPS = "heat_pumps"
CONF_ZONES = "zones"
CONF_SETPOINTS = "setpoints"
CONF_CONTROLS = "controls"
# Which virtual digital input carries SmartGrid A and B. Site-specific: the
# manual says a terminal's DI number is configured in the controller's own
# menus, so it can only be told to us or found with `discover-di`.
CONF_SMARTGRID_A = "smartgrid_a"
CONF_SMARTGRID_B = "smartgrid_b"
SMARTGRID_UNUSED = "none"
CONF_SUBSYSTEMS = "subsystems"
CONF_MODEL = "model"

# The controller reports its own model here. Not 62207 sSystemType - that is
# the hydraulic layout configured in its menus, not the model.
REG_PRODUCT_TYPE = 62253

MANUFACTURER = "CTC"

# A guaranteed-live register (outdoor temperature). On this controller a
# nonexistent register and a dead link are indistinguishable (both are silence),
# so connection probes must use an address known to exist.
PROBE_REGISTER = 62000

# The manual caps a transfer at 100 registers.
MAX_BLOCK = 100

# ---------------------------------------------------------------------------
# WARNING: writes wear the controller out.
#
# Every register in the tables below is one of the controller's *stored
# parameters*, and the BMS manual is explicit about the cost of writing them:
#
#     "These parameters must not be changed a lot of times. If you do so you
#     risk breaking the controller of the heat pump installation. There is a
#     limit to the amount of write cycles!"
#
# So these entities are for settings a human changes, not for closed-loop
# control. The manual's own answer for anything that must change often is the
# 1000-range control registers, which are free of write-cycle cost and expire
# after 5 minutes; those live in controls.py and hold.py, behind CONF_CONTROLS.
# Adding a register to a table below because an automation wants to drive it is
# the wrong fix - the right one is a control register.
#
# CtcEntity.async_write_raw is the single write path and drops a write that
# would not change the register, so a repeating automation costs nothing while
# its value is steady. That is a backstop, not a licence: an automation that
# actually oscillates a value still writes every time it changes.
# ---------------------------------------------------------------------------

# Curated writable setpoints exposed as number entities, with conservative
# (min, max, step) limits in engineering units. Deliberately small: a wrong
# write to a heat pump is a real-world risk. Enum-like RW registers belong in
# SELECT_SYSTEM below, not here. v2 idea: derive limits from the
# parameter-metadata records in the 60000 range once their layout is verified
# on hardware.
SETPOINT_SYSTEM: dict[int, tuple[float, float, float]] = {
    61501: (30.0, 65.0, 0.5),  # sDHWManStopTemp - manual stop temp hot water
}
SETPOINT_HP_FIELDS: dict[str, tuple[float, float, float]] = {
    "RPSMax": (20.0, 120.0, 1.0),
}
SETPOINT_ZONE_FIELDS: dict[str, tuple[float, float, float]] = {
    "SetPoint": (10.0, 30.0, 0.5),  # room temperature setpoint
    "ReduceTempNight": (0.0, 20.0, 0.5),
}

# Enum-like RW registers exposed as select entities: {raw value: option}. Only
# registers whose *complete* value set the manual spells out belong here - the
# controller silently accepts out-of-range writes, so an option that maps to an
# undocumented value is a real-world risk. Values are the raw register contents
# (every enum register in the map has scale 1). Options must be unique per
# register: the write path reverses this map.
#
# Keyed like the setpoint tables: by register number for the flat map, by field
# name for the HP/Zone arrays - a property of the register's shape, not of the
# device it is displayed on. The hcN heating programs are flat registers even
# though groups.py shows them on a Zone device, so they are keyed by number.
_MODE_AUTO_ON_OFF = {0: "Auto", 1: "On", 2: "Off"}
_PROGRAM = {0: "Economy", 1: "Normal", 2: "Comfort", 3: "Custom"}

SELECT_SYSTEM: dict[int, dict[int, str]] = {
    # sDHWMode ("Hot water mode 0=Ecomony, 1=Normal, 2=Comfort" - the manual's
    # typo, not ours).
    61500: {0: "Economy", 1: "Normal", 2: "Comfort"},
    61587: _MODE_AUTO_ON_OFF,  # exbMode - external boiler
    61671: _PROGRAM,  # heating program HC1..HC4 (shown on the Zone devices)
    61672: _PROGRAM,
    61673: _PROGRAM,
    61674: _PROGRAM,
}
SELECT_HP_FIELDS: dict[str, dict[int, str]] = {}
SELECT_ZONE_FIELDS: dict[str, dict[int, str]] = {
    "Mode": _MODE_AUTO_ON_OFF,  # 61542-61545 heating mode
}

# Boolean RW registers exposed as switches: field/register -> (on, off) raw
# values. Note the polarity: the register is named for the *blocked* state and
# reads 0 when blocked, so switching the entity on writes 0. Keeping the
# register's own sense avoids an entity whose name and value disagree.
SWITCH_HP_FIELDS: dict[str, tuple[int, int]] = {
    "Blocked": (0, 1),  # 61521-61530 "0=Blocked, 1=Allowed"
}
SWITCH_ZONE_FIELDS: dict[str, tuple[int, int]] = {}
SWITCH_SYSTEM: dict[int, tuple[int, int]] = {}

# RW registers surfaced as read-only sensors: the value is trustworthy, but
# what to *write* isn't established. The manual names these as booleans yet
# documents no value legend, so they are readable until the polarity is
# confirmed against hardware - then they move to SWITCH_SYSTEM.
READ_ONLY_RW: frozenset[int] = frozenset(
    {
        61656,  # sVentNightcoolValue "Turn night cooling on or off"
        61658,  # pool_enable "Pool Enable"
    }
)

# Read-only registers whose value is a documented state, as enum sensors
# (SensorDeviceClass.ENUM): the sensor reports "Compressor on, heating" rather
# than "3". Same keying as the SELECT_* tables. Labels follow the manual, with
# its typos fixed ("redy", "Comperssor"); an undocumented value reads unknown
# rather than a made-up option, so gaps in a legend are safe.
#
# NB these legends came off the PDF, not dev/bms_registers.json: parse_bms.py
# truncates descriptions at 160 characters, which loses the tail of the two
# longest ones (62017 stops at "5=", 62365 mid-word).
_SHUNT = {0: "Close", 1: "Inactive", 2: "Open"}  # drive direction - see below
_EL_RELAY = {0: "None", 1: "A", 2: "B", 3: "A + B"}  # "Bit 0: A, Bit 1: B"

ENUM_SYSTEM: dict[int, dict[int, str]] = {
    62005: {  # sStatus - what the installation is currently doing
        0: "HP upper",
        1: "HP lower",
        2: "Add",
        3: "HP + Add",
        4: "HC",
        5: "DHW",
        6: "Pool",
        7: "Off",
        8: "Heating mix",
        9: "Wood",
        10: "DHW/HC",
        11: "Cooling",
        12: "Swap",
    },
    62301: {  # SGMode (smart grid)
        0: "None/Normal",
        1: "Block",
        2: "Low price",
        3: "High capacity",  # the manual abbreviates this "High cap"
    },
    62315: _EL_RELAY,
    62316: _EL_RELAY,
    62317: _EL_RELAY,
    62319: _SHUNT,  # EVK
    62320: _SHUNT,  # ExtBoiler
    62365: {  # periodic extra DHW status
        0: "Not defined",
        1: "Active",
        2: "Off, normal",
        3: "Off, last DHW increase failed",
    },
}
ENUM_HP_FIELDS: dict[str, dict[int, str]] = {
    # 62017-62026. The legend jumps 8 -> 30; the gap reads unknown.
    "Status": {
        0: "Compressor off, start delay",
        1: "Compressor off, ready to start",
        2: "Compressor wait until flow",
        3: "Compressor on, heating",
        4: "Defrost active",
        5: "Compressor on, cooling",
        6: "Compressor off, blocked",
        7: "Compressor off, alarm",
        8: "Function test",
        30: "HP not defined",
        31: "Compressor not enabled",
        32: "Communication error",
        33: "Charge DHW",
    },
}
ENUM_ZONE_FIELDS: dict[str, dict[int, str]] = {
    "Status": {  # 62246-62249
        0: "Heating off",
        1: "Vacation",
        2: "Night reduction",
        3: "On (normal mode)",
    },
    "ShuntState": _SHUNT,  # 62308-62311
}

# Read-only registers that are on/off rather than numeric, as binary sensors:
# register -> BinarySensorDeviceClass value (None for a plain on/off).
#
# Only registers that are boolean *by evidence*: a documented 0/1 legend, or a
# name that states an on/off output where the manual annotates its percentage
# siblings ("DHWPump: 0-100") and leaves these bare. Registers whose value set
# is merely assumed stay sensors - see the note below.
BINARY_SYSTEM: dict[int, str | None] = {
    # sDHWPump "DHW circulation" - bare where its sibling 62323 "DHWPump: 0-100"
    # is annotated, and observed only ever 0 or 1 in the field. The "%" it
    # carries in the generated map is inferred from the name ending in "pump"
    # (gen_registers.py), not from the manual.
    62016: "running",
    62181: "running",  # sunStatus, documented "0=Off, 1=On"
    62304: "running",  # RadiatorPump1
    62305: "running",  # RadiatorPump2
    62306: "running",  # RadiatorPump3_G3
    62307: "running",  # RadiatorPump4_G4
    62312: "running",  # PumpG41 Extern DHW
    62314: "running",  # ExtBoilerOn
    62318: "running",  # ElHeater3_6kW
    62322: None,  # Active Cooling: Demand - a request, not a running device
}

# Deliberately left as sensors, because "nonzero" would misreport them:
#   62315-62317 EL1/EL2/EL3 relay - a 2-bit field (bit 0 = A, bit 1 = B)
#   62323 DHWPump - documented 0-100, a percentage
#   62324/62325 solar tank / bedrock selection - a choice, not a state
#   62327/62328 E1/E4 - the manual gives no semantics at all
#   62321 Active Cooling: Valve - reads 25600 (0x6400) on the EcoLogic M, so
#          whatever it holds, it is not a two-state valve
#   62177 ehsStatus / 62178 poolStatus - named "Mode" but the manual documents
#          no legend for either, so they cannot become enums

# Read-only two-position valves as valve entities: register -> (raw value that
# means open, ValveDeviceClass). Every valve and shunt register in the map is
# read-only, so these report state and expose no OPEN/CLOSE feature.
#
# Excluded on purpose: the six modulating shunts (Zone ShuntState 62308-62311,
# EVK 62319, ExtBoiler 62320) report drive direction - "0=Close, 1=Inactive,
# 2=Open" is which way the actuator is being driven, not where the valve sits.
# HA's valve domain has no state for "holding at an unknown position", which is
# what Inactive means and what a mixing valve does most of the time, so they
# stay sensors reporting the documented enum.
VALVE_SYSTEM: dict[int, tuple[int, str]] = {
    62313: (1, "water"),  # HotWaterValve - diverted to DHW when 1
    62326: (1, "water"),  # HotWaterValve2
}
