"""Pure decode/encode tests - no Home Assistant involved."""

from custom_components.ctc_bms.decode import (
    SENTINELS,
    decode_value,
    encode_value,
    is_sentinel,
    to_signed16,
)
from custom_components.ctc_bms.registers import Reg

TEMP = Reg(62000, "sOutsideTemp", "", "S16", 0.1, "°C", "R", "System")
U32 = Reg(62186, "sTotalTime", "", "U32", 1.0, "h", "R", "System")
S32 = Reg(62186, "x", "", "S32", 1.0, "h", "R", "System")


def test_signed16():
    assert to_signed16(0x0001) == 1
    assert to_signed16(0xFFFF) == -1
    assert to_signed16(0x8000) == -32768


def test_negative_temperature():
    assert decode_value(TEMP, [0xFFCB]) == -5.3


def test_32bit_is_lsb_first():
    # 32-bit values are LSB first, MSB second: (MSB << 16) | LSB. This is the
    # deliberate anti-convention of the controller - do not "fix" it.
    assert decode_value(U32, [0x3039, 0x0000]) == 12345
    assert decode_value(U32, [0x0000, 0x0001]) == 65536


def test_s32_sign():
    assert decode_value(S32, [0xFFFF, 0xFFFF]) == -1


def test_sentinels():
    assert is_sentinel(TEMP, [55536])
    assert is_sentinel(TEMP, [55537])
    assert not is_sentinel(TEMP, [0])
    # A 32-bit register can never be sentinel by its first word alone.
    assert not is_sentinel(U32, [55536, 0])
    assert SENTINELS == {55536, 55537}


def test_encode_round_trip():
    assert encode_value(TEMP, 50.0) == 500
    assert decode_value(TEMP, [encode_value(TEMP, -5.3)]) == -5.3
    whole = Reg(61504, "HeatMaxTime", "", "S16", 1.0, "h", "RW", "System")
    assert encode_value(whole, 40) == 40
