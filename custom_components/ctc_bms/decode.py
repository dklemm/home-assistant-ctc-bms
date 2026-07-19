"""Raw register words <-> engineering values.

Pure functions, no I/O; shared by entities, config flow and tests.
"""

from __future__ import annotations

from .registers import Reg

# An unfitted sensor reports -9999/-10000 rather than an error. Numerically
# nonzero, so it must never be mistaken for real data.
SENTINELS = {55536, 55537}  # -10000, -9999


def to_signed16(raw: int) -> int:
    return raw - 0x10000 if raw >= 0x8000 else raw


def decode_value(reg: Reg, words: list[int]) -> float:
    """Raw register words -> scaled engineering value.

    32-bit values are stored LSB first, MSB second (value = MSB << 16 | LSB),
    which is little-endian word order - the opposite of the usual Modbus
    convention, so don't "fix" this to big-endian.
    """
    if reg.count == 2:
        val = (words[1] << 16) | words[0]
        if reg.dtype == "S32" and val >= 0x80000000:
            val -= 0x100000000
    else:
        val = to_signed16(words[0]) if reg.dtype == "S16" else words[0]
    if reg.scale == 1:
        return val  # statuses/counters stay ints ("3", not "3.0")
    # Round away binary-float noise (-53 * 0.1 = -5.300000000000001); the
    # scales are 0.5, 0.1 and 0.01 here, so 4 decimals is already generous.
    return round(val * reg.scale, 4)


def is_sentinel(reg: Reg, words: list[int]) -> bool:
    """True when the value is the "no sensor fitted" marker."""
    return reg.count == 1 and words[0] in SENTINELS


def encode_value(reg: Reg, value: float) -> int:
    """Engineering value -> raw 16-bit word for an FC16 write.

    Only 16-bit setpoints are writable; every RW register in the map is one
    word.
    """
    if reg.count != 1:
        raise ValueError(f"register {reg.number} is 32-bit and not writable")
    return round(value / reg.scale) & 0xFFFF
