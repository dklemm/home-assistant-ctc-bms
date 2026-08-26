#!/usr/bin/env python3
"""Tiny local Modbus TCP server that pretends to be a CTC heat pump.

Serves the CTC BMS map on holding registers (FC03) so ctc_modbus_test.py can be
exercised without the real device. Seeds HP1 with plausible running values and
leaves HP2..HP10 at zero, which is what a single-pump installation looks like.

    python fake_ctc_server.py            # serves on 127.0.0.1:5020
    python ctc_modbus_test.py --host 127.0.0.1 --port 5020 verify
"""
import argparse
import time

from pymodbus.datastore import (ModbusDeviceContext, ModbusSequentialDataBlock,
                                ModbusServerContext)
from pymodbus.server import StartTcpServer

# One block spans the control registers and the BMS map. 1000 rather than 61500
# so the 1000-1100 "control parameters" are servable; the intervening addresses
# answer 0 where real hardware is silent, which the simulator already does for
# unimplemented 61500-range registers, so it is no new kind of infidelity.
BASE = 1000             # lowest register we serve
TOP = 62600             # one past the highest

CONTROL_LO, CONTROL_HI = 1000, 1100
VDI = 1100              # virtual digital inputs, bits 0-7 = DI0-DI7

NOT_FITTED = 55536      # -10000: the controller's "no sensor connected" sentinel


def s16(v: int) -> int:
    return v & 0xFFFF


class ControlSemantics:
    """Models what makes the control registers different from the BMS map.

    Two behaviours, without which the interesting half of the feature cannot be
    exercised without hardware:

    * **Expiry.** A control register not re-written within `expiry` seconds
      reverts to 0, as the manual says the controller does after 5 minutes.
    * **Read-back mirrors.** Real control registers are write-only; what proves
      a write landed is some *other* register moving. Mirror the documented
      pairs so the whole loop is observable.

    Implemented as a pymodbus `SimAction` rather than a data-block subclass:
    since 3.14 `ModbusDeviceContext` deep-copies the block into a `SimDevice` at
    construction, so an overridden `setValues` is simply never called. The hook
    fires on *every* access - reads carry `values=None`, writes carry the
    incoming words and fire before the store is updated - which also lets expiry
    be evaluated lazily on access instead of from a background thread.
    """

    def __init__(self, expiry: float, sg_bits: tuple[int, int],
                 dhw_bit: int | None):
        self.expiry = expiry
        self.sg_bits = sg_bits
        self.dhw_bit = dhw_bit
        self.written: dict[int, float] = {}

    def as_action(self):
        """A bare `async def` closure - pymodbus rejects anything else.

        It checks `iscoroutinefunction`, which is False for an instance with an
        `async def __call__`, so the state has to live outside the callable.
        """
        async def action(fc, base, address, count, registers, values):
            return self.handle(fc, base, address, count, registers, values)
        return action

    def handle(self, fc, base, address, count, registers, values):
        now = time.monotonic()
        for number, when in list(self.written.items()):
            if now - when > self.expiry:
                del self.written[number]
                registers[number - base] = 0
                self._mirror(registers, base, number, 0)
                print(f"  [sim] {number} expired after {self.expiry:g}s -> 0")
        if values is None:
            return None                      # a read
        for i in range(count):
            number = address + i
            if CONTROL_LO <= number <= CONTROL_HI:
                self.written[number] = now
                self._mirror(registers, base, number, values[i])
        return None

    def _mirror(self, registers, base: int, number: int, word: int) -> None:
        def put(target: int, value: int) -> None:
            registers[target - base] = value

        if number == VDI:
            a, b = self.sg_bits
            closed = ((word >> a) & 1, (word >> b) & 1)
            # The manual's SmartGrid truth table, as read back on 62301 SGMode.
            put(62301, {(0, 0): 0, (0, 1): 2, (1, 1): 3, (1, 0): 1}[closed])
            if self.dhw_bit is not None:
                put(62016, (word >> self.dhw_bit) & 1)
        elif number == 1033:
            put(62001, word)                 # DHW tank setpoint -> stop temp
        elif number == 1007:
            put(61500, word)                 # DHW mode
        elif number == 1002:
            put(62193, word)                 # max RPS -> current RPS


def main():
    values = [0] * (TOP - BASE)

    def put(addr: int, word: int):
        values[addr - BASE] = word

    def put32(addr: int, value: int):
        """32-bit values are stored LSB first, MSB second."""
        put(addr, value & 0xFFFF)
        put(addr + 1, (value >> 16) & 0xFFFF)

    # setpoints (RW in the manual; this tool only ever reads them)
    put(61500, 1)              # DHW mode = Normal
    put(61501, s16(500))       # Manual stop temp hot water = 50.0 C
    put(61504, s16(40))        # Max time heating

    # system
    put(62000, s16(-53))       # Outside temp = -5.3 C (exercises signed decode)
    put(62001, s16(500))       # DHW stop temp = 50.0 C
    put(62005, 5)              # System status
    put(62006, s16(350))       # Radiator water = 35.0 C
    put(62007, s16(352))       # Heating system 1 setpoint primary flow = 35.2 C
    put(62011, s16(280))       # Heating system 1 primary flow = 28.0 C
    put(62012, NOT_FITTED)     # Heating system 2 - no sensor fitted
    put(62015, s16(290))       # Return temp = 29.0 C
    put(62167, s16(-124))      # Degree minutes
    put(62186, 0)              # total operation time, LSB/MSB below
    put32(62186, 8)            # 8 h
    put(62276, s16(480))       # DHW actual = 48.0 C

    # heat pump 1 (running)
    put(62017, 3)              # hp1Status = 3 (compressor on, heating)
    put(62027, s16(466))       # HP1 in = 46.6 C
    put(62037, s16(565))       # HP1 out = 56.5 C
    put(62047, s16(638))       # HP1 discharge gas = 63.8 C
    put(62057, s16(103))       # HP1 suction gas = 10.3 C
    put(62067, s16(186))       # HP1 high pressure = 18.6 bar
    put(62077, s16(52))        # HP1 low pressure = 5.2 bar
    put(62087, s16(113))       # HP1 brine in = 11.3 C
    put(62097, s16(85))        # HP1 brine out = 8.5 C
    put(62107, s16(500))       # HP1 charge pump = 50.0 %
    put(62117, s16(327))       # HP1 brine pump = 32.7 %
    put(62147, s16(186))       # HP1 outside temp = 18.6 C
    put(62193, s16(434))       # HP1 current RPS = 43.4
    put32(62214, 12345)        # HP1 compressor operating time = 12345 h (32-bit)
    put(62254, 11)             # HP1 type
    put(62331, s16(50))        # HP1 power consumption = 5.0 kW

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5020)
    ap.add_argument("--expiry", type=float, default=300.0,
                    help="seconds before an un-refreshed control register "
                         "reverts to 0 (the controller's is 300; use 30 to "
                         "watch it happen)")
    ap.add_argument("--sg-bits", default="6,7", metavar="A,B",
                    help="which DI bits mirror to 62301 as SmartGrid A/B")
    ap.add_argument("--dhw-bit", type=int, default=3,
                    help="which DI bit mirrors to 62016 DHW circulation, "
                         "standing in for a site's K22")
    args = ap.parse_args()

    sg_bits = tuple(int(b) for b in args.sg_bits.replace(",", " ").split())

    # ModbusDeviceContext applies the legacy address+1 lookup into the block
    # (pymodbus 3.14 dropped the zero_mode knob but not the offset), so basing
    # the block at BASE+1 is what makes Modbus address N return values[N-BASE].
    block = ModbusSequentialDataBlock(BASE + 1, values)
    device = ModbusDeviceContext(hr=block, ir=block)
    context = ModbusServerContext(devices=device, single=True)
    # Attached after construction: ModbusDeviceContext copies the block into the
    # SimDevice, and the SimDevice is where the action hook lives.
    context.simdevices[0].action = ControlSemantics(
        args.expiry, sg_bits, args.dhw_bit).as_action()

    print(f"Fake CTC serving on {args.host}:{args.port} (Ctrl+C to stop)")
    print(f"  registers {BASE}-{TOP - 1}; controls {CONTROL_LO}-{CONTROL_HI} "
          f"expire after {args.expiry}s")
    print(f"  1100 bits {sg_bits[0]}/{sg_bits[1]} -> 62301 SGMode, "
          f"bit {args.dhw_bit} -> 62016 DHW circulation")
    StartTcpServer(context=context, address=(args.host, args.port))


if __name__ == "__main__":
    main()
