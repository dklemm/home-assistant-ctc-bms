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

MANUFACTURER = "CTC"

# A guaranteed-live register (outdoor temperature). On this controller a
# nonexistent register and a dead link are indistinguishable (both are silence),
# so connection probes must use an address known to exist.
PROBE_REGISTER = 62000

# The manual caps a transfer at 100 registers.
MAX_BLOCK = 100

# Curated writable setpoints exposed as number entities, with conservative
# (min, max, step) limits in engineering units. Deliberately small for v1:
# enum-like RW registers (modes, blocking) are excluded until they can become
# proper select/switch entities, and a wrong write to a heat pump is a
# real-world risk. v2 idea: derive limits from the parameter-metadata records
# in the 60000 range once their layout is verified on hardware.
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
