#!/usr/bin/env python3
"""Tiny local Modbus TCP server that pretends to be a CTC heat pump.

Serves the CTC BMS map on holding registers (FC03) so ctc_modbus_test.py can be
exercised without the real device. Seeds HP1 with plausible running values and
leaves HP2..HP10 at zero, which is what a single-pump installation looks like.

    python fake_ctc_server.py            # serves on 127.0.0.1:5020
    python ctc_modbus_test.py --host 127.0.0.1 --port 5020 verify
"""
import argparse

from pymodbus.datastore import (ModbusDeviceContext, ModbusSequentialDataBlock,
                                ModbusServerContext)
from pymodbus.server import StartTcpServer

BASE = 61500            # lowest register we serve
TOP = 62600             # one past the highest

NOT_FITTED = 55536      # -10000: the controller's "no sensor connected" sentinel


def s16(v: int) -> int:
    return v & 0xFFFF


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

    # ModbusDeviceContext applies the legacy address+1 lookup into the block
    # (pymodbus 3.14 dropped the zero_mode knob but not the offset), so basing
    # the block at BASE+1 is what makes Modbus address N return values[N-BASE].
    block = ModbusSequentialDataBlock(BASE + 1, values)
    device = ModbusDeviceContext(hr=block, ir=block)
    context = ModbusServerContext(devices=device, single=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5020)
    args = ap.parse_args()
    print(f"Fake CTC serving on {args.host}:{args.port} (Ctrl+C to stop)")
    StartTcpServer(context=context, address=(args.host, args.port))


if __name__ == "__main__":
    main()
