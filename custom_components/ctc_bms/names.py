"""Entity names, written by hand.

The manual gives no display names. Its Name column is machine shorthand
(`CurrRPS`, `sSetPDHW`, `sDM`) and *blank* on a good number of rows, where
gen_registers.py falls back to a 40-character slug of the description - which
drags the value legend into the name (`evk_shunt_state_0_close_1_inactive_2_ope`)
and flattens real camelCase (`hotwatervalve`). So these are not overrides of
anything: they are the only display names that exist, and they live here for the
same reason groups.py and models.py do - hand-authored, and regenerating
registers.py must not clobber them.

Editing has to work from a clone, which rules out the two tempting homes: the
generator would need the manual's PDF, which is copyrighted and not committed,
and dev/bms_registers.json is rewritten wholesale on the next parse (and is the
record of what the manual says, not of what we call things).

Conventions:

- HA sentence case ("Discharge gas"), acronyms excepted ("Max RPS").
- Omit what `has_entity_name` already supplies from the device: hot water's
  sDHWTemp is just "Temperature", because HA shows "CTC Hot Water Temperature".
- Say what the register is, not that it is current: the entity state always is.
  Hence HP `CurrRPS` -> "RPS".
- Unique within a device; entity.register_entity_name() will derive a name for
  anything unlisted, but tests/test_names.py asserts nothing shipped needs to.

Keyed like the curated tables in const.py - by register number for the flat map,
by field name for the HP/Zone arrays, which is a property of the register's
*shape*, not of the device it is displayed on. The hcN heating programs are flat
registers even though groups.py shows them on a Zone device, so they are keyed
by number.
"""

from __future__ import annotations

from .registers import Reg


NAME_SYSTEM: dict[int, str] = {
    # -- controller ("CTC Heat Pump System")
    62000: "Outdoor temperature",
    62004: "Mixing valve delay",
    62005: "Status",
    62006: "Radiator water temperature",
    62015: "Return temperature",
    62167: "Degree minutes",
    62168: "Immersion heater power",
    62169: "Lower immersion heater power",
    62170: "Maximum current",  # the manual's "Maximum current", not a live read
    62171: "Current L1",
    62172: "Current L2",
    62173: "Current L3",
    62174: "Differential thermostat pump",
    62175: "Differential thermostat temperature",
    62185: "Tank timer",
    62186: "Total operating time",
    62191: "Immersion heater energy",
    62192: "Function test",
    62207: "System type",
    62244: "Display software version (month/day)",
    62245: "Display software version (year)",
    62253: "Product type",
    62274: "Lower tank setpoint",
    62278: "Upper tank setpoint with immersion heater",
    62301: "Smart grid mode",
    62302: "Elspot price per MWh",
    62303: "Elspot price per MWh, decimals",
    62304: "Radiator pump 1",
    62305: "Radiator pump 2",
    62306: "Radiator pump 3 (G3)",
    62307: "Radiator pump 4 (G4)",
    # -- hot water ("CTC Hot Water")
    61500: "Mode",
    61501: "Manual stop temperature",
    62001: "Stop temperature",
    62002: "Outlet temperature setpoint",
    62003: "Temperature",
    62016: "Circulation pump",
    62252: "External buffer tank temperature",
    62275: "Lower temperature",
    62276: "Upper temperature",
    62279: "Capacity",
    62312: "External pump (G41)",
    62313: "Diverter valve",
    62323: "Pump speed",  # 0-100, unlike 62016's circulation pump
    62326: "Diverter valve 2",
    62363: "Tank top temperature",
    62365: "Periodic extra status",
    # -- solar ("CTC Solar")
    62181: "Status",
    62182: "Temperature out",
    62183: "Temperature in",
    62184: "Panel pump",
    62277: "Tank coil temperature",
    62324: "Tank selection",
    62325: "Bedrock selection",
    # -- pool ("CTC Pool")
    61658: "Enabled",
    62178: "Mode",
    62179: "Temperature",
    62180: "Stop temperature",
    # -- cooling ("CTC Cooling")
    62288: "Tank setpoint",
    62289: "Tank temperature",
    62290: "Active cooling return temperature",
    62321: "Active cooling valve",
    62322: "Active cooling demand",
    62330: "Room temperature",
    62364: "Tank top temperature",
    # -- ventilation ("CTC Ventilation")
    61656: "Night cooling enabled",
    62280: "Exhaust fan speed",
    62281: "Highest measured CO2",
    62282: "Highest measured humidity",
    62283: "Days until filter maintenance",
    62284: "Night cooling status",
    # -- additional heat ("CTC Additional Heat")
    61587: "External boiler mode",
    62176: "EHS temperature",
    62177: "EHS mode",
    62208: "Wood boiler flue gas temperature (B8)",
    62209: "Wood boiler temperature (B9)",
    62210: "E1 boiler temperature (B9)",
    62211: "E1 boiler out temperature (B10)",
    62212: "E2 steps",
    62213: "E3 status",
    62250: "External buffer tank upper temperature (B41)",
    62251: "External buffer tank lower temperature (B42)",
    62314: "External boiler on",
    62315: "EL1 relay",
    62316: "EL2 relay",
    62317: "EL3 relay",
    62318: "Immersion heater 3.6 kW",
    62319: "EVK shunt state",
    62320: "External boiler shunt state",
    62327: "E4",
    62328: "E1",
    62362: "Heating buffer tank top temperature",
    # -- heating programs: flat registers shown on the Zone devices
    61671: "Heating program",
    61672: "Heating program",
    61673: "Heating program",
    61674: "Heating program",
}

# Displayed on "CTC Heat Pump N", so no name repeats the pump.
NAME_HP_FIELDS: dict[str, str] = {
    "Blocked": "Blocked",
    "RPSMax": "Max RPS",
    "Status": "Status",
    # The manual's "HP in"/"HP out": the heating-carrier side, as opposed to the
    # separately named brine side.
    "TempIn": "Heating water in",
    "TempOut": "Heating water out",
    "DischargeGas": "Discharge gas",
    "SuctionGas": "Suction gas",
    "HighPressure": "High pressure",
    "LowPressure": "Low pressure",
    "BrineTempIn": "Brine in",
    "BrineTempOut": "Brine out",
    "ChargePump": "Charge pump",
    "BrinePump": "Brine pump",
    "Fan": "Fan",
    "DefrostTimer": "Defrost timer",
    "OutsideTemp": "Outdoor temperature",
    "SWVersion": "Software version",
    "CurrRPS": "RPS",  # the entity state is the current value by definition
    "CompressorTime": "Compressor operating time",
    "CompressorTime24H": "Compressor operating time last 24 h",
    "Type": "Type",
    "CompressorModel": "Compressor model",
    "PrimarySystemFlow": "Primary system flow",
    "PowerConsumption": "Power",
    "Energy": "Energy",
}

# Displayed on "CTC Heating System N". Includes the writable fields no curated
# table names yet, so promoting one doesn't need a name added here too.
NAME_ZONE_FIELDS: dict[str, str] = {
    "SetPoint": "Room temperature setpoint",
    "Inclination": "Curve inclination",
    "Adjustment": "Curve adjustment",
    "PrimaryFlowMax": "Max primary flow temperature",
    "PrimaryFlowMin": "Min primary flow temperature",
    "Mode": "Heating mode",
    "HeatingOffTemp": "Heating off outdoor temperature",
    "HeatingOffTime": "Heating off time",
    "ReduceTempNight": "Night room temperature reduction",
    "PrimaryFlowReduceNight": "Night primary flow reduction",
    "NightReduceOffTemp": "Night reduction off outdoor temperature",
    "AlarmLowRoomTemp": "Low room temperature alarm",
    "ReduceTempVac": "Holiday room temperature reduction",
    "PrimaryFlowReduceVac": "Holiday primary flow reduction",
    "HeatingOnTime": "Heating on time",
    "Temp": "Primary flow setpoint",
    "PrimaryFlow": "Primary flow temperature",
    "TempCurr": "Room temperature",
    "Status": "Status",
    "ShuntState": "Shunt state",
}


def name_for(reg: Reg) -> str | None:
    """The curated entity name for a register, or None if it has none."""
    if reg.device == "System":
        return NAME_SYSTEM.get(reg.number)
    field = reg.name.split(" ", 1)[1] if " " in reg.name else reg.name
    table = NAME_HP_FIELDS if reg.device.startswith("HP") else NAME_ZONE_FIELDS
    return table.get(field)
