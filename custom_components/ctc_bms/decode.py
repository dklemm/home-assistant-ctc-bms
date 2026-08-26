"""Raw register words <-> engineering values.

Pure functions, no I/O; shared by entities, config flow and tests.
"""

from __future__ import annotations

from modbus_connection.decode import (
    decode_int16,
    decode_int32,
    decode_uint16,
    decode_uint32,
)

from .overrides import override_for
from .registers import Reg

# An unfitted sensor reports -9999/-10000 rather than an error. Numerically
# nonzero, so it must never be mistaken for real data.
SENTINELS = {55536, 55537}  # -10000, -9999


def decode_value(reg: Reg, words: list[int]) -> float:
    """Raw register words -> scaled engineering value.

    32-bit values are stored LSB first, MSB second - little-endian word order,
    the opposite of the usual Modbus convention, so don't "fix" this to "big".
    """
    if reg.count == 2:
        decode = decode_int32 if reg.dtype == "S32" else decode_uint32
        val = decode(words, word_order="little")
    else:
        val = decode_int16(words) if reg.dtype == "S16" else decode_uint16(words)
    if reg.scale == 1:
        return val  # statuses/counters stay ints ("3", not "3.0")
    # Round away binary-float noise (-53 * 0.1 = -5.300000000000001); the
    # scales are 0.5, 0.1 and 0.01 here, so 4 decimals is already generous.
    return round(val * reg.scale, 4)


def is_sentinel(reg: Reg, words: list[int]) -> bool:
    """True when the register answered but holds no reading.

    Two markers, because the controller uses two. -9999/-10000 is the
    documented "no sensor fitted" one and applies everywhere. A plain 0 is the
    undocumented one, and applies *only* to the registers overrides.py marks
    `zero_is_unknown` - a handful of DHW temperatures the controller parks at 0
    when its tank arrangement doesn't have them. Generalising that to every
    register would erase real readings; 0 is a normal outdoor temperature and a
    real (idle) compressor speed.
    """
    if reg.count != 1:
        return False
    if words[0] in SENTINELS:
        return True
    return words[0] == 0 and override_for(reg.number).zero_is_unknown


def is_present(regs: list[Reg], words: dict[int, int]) -> bool:
    """Whether the hardware behind these registers looks fitted.

    Every register answers whether or not the hardware exists (absent hardware
    reads 0), so presence is decided by *real nonzero data* - and the
    -9999/-10000 no-sensor sentinel is evidence of absence, not data. Requiring
    two data registers keeps a single stray value from creating a device.

    A hint, not a verdict: some subsystems (solar, pool, wood boiler) report
    plausible-looking values on controllers that have none, which is why the
    options flow lets the user override the result.
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


def encode_value(reg: Reg, value: float) -> int:
    """Engineering value -> raw 16-bit word for an FC16 write.

    Only 16-bit setpoints are writable; every RW register in the map is one
    word.
    """
    if reg.count != 1:
        raise ValueError(f"register {reg.number} is 32-bit and not writable")
    return round(value / reg.scale) & 0xFFFF
