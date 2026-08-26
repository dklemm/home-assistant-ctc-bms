"""Pure decode/encode tests - no Home Assistant involved."""

from custom_components.ctc_bms.decode import (
    SENTINELS,
    decode_value,
    encode_value,
    is_present,
    is_sentinel,
)
from custom_components.ctc_bms.registers import Reg

TEMP = Reg(62000, "sOutsideTemp", "", "S16", 0.1, "°C", "R", "System")
U32 = Reg(62186, "sTotalTime", "", "U32", 1.0, "h", "R", "System")
S32 = Reg(62186, "x", "", "S32", 1.0, "h", "R", "System")


def test_negative_temperature():
    assert decode_value(TEMP, [0xFFCB]) == -5.3
    assert decode_value(TEMP, [0x8000]) == -3276.8


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


def test_zero_is_unknown_only_where_overrides_say_so():
    # The controller parks the DHW temperatures its tank arrangement doesn't
    # have at 0 rather than at the sentinel, so 0 there is "no reading".
    dhw = Reg(62003, "sDHWTemp", "", "S16", 0.1, "°C", "R", "System")
    assert is_sentinel(dhw, [0])
    assert not is_sentinel(dhw, [610])   # a real 61.0 C tank still decodes
    # And nowhere else: 0 C outdoors is an ordinary morning, not a fault.
    assert not is_sentinel(TEMP, [0])


def test_a_zero_unknown_register_is_still_present_hardware():
    # is_present() counts *nonzero* data, so marking a register
    # zero_is_unknown must not change which devices get created.
    regs = [
        Reg(62003, "sDHWTemp", "", "S16", 0.1, "°C", "R", "System"),
        Reg(62276, "sDHWUpperTemp", "", "S16", 0.1, "°C", "R", "System"),
        Reg(62001, "sStopTempDHW", "", "S16", 0.1, "°C", "R", "System"),
    ]
    # The EcoLogic M's real readings: only the upper sensor carries the tank.
    assert is_present(regs, {62003: 0, 62276: 610, 62001: 600})


def test_encode_round_trip():
    assert encode_value(TEMP, 50.0) == 500
    assert decode_value(TEMP, [encode_value(TEMP, -5.3)]) == -5.3
    whole = Reg(61504, "HeatMaxTime", "", "S16", 1.0, "h", "RW", "System")
    assert encode_value(whole, 40) == 40
