"""Heat a heat pump delivers, derived from its flow and temperatures.

Pure functions, no I/O. The controller reports electrical power and energy but
not heat; this is the calorimetric sum over registers we already poll.
"""

from __future__ import annotations

# kJ/(L·K): density × specific heat of water at heating temperatures. The
# heating side is plain water; the glycol brine is on the other side of the
# pump, and has no flow register to calculate with anyway.
WATER_HEAT_CAPACITY = 4.18


def heat_output_kw(
    flow_lpm: float | None,
    t_in: float | None,
    t_out: float | None,
    rps: float | None,
) -> float | None:
    """Heat output in kW, signed; None if any input is unknown.

    0 while the compressor is stopped: the charge pump can still run, and its
    ΔT is then a few tenths of sensor noise, not heat. A negative result is
    real - a defrost takes heat back out of the system.
    """
    if None in (flow_lpm, t_in, t_out, rps):
        return None
    if rps <= 0:
        return 0.0
    return round(flow_lpm / 60 * WATER_HEAT_CAPACITY * (t_out - t_in), 3)
