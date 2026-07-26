#!/usr/bin/env python3
"""
CTC Heat Pump - Modbus TCP register verification tool
=====================================================

Reads the CTC BMS registers off a live heat pump. Built on pymodbus (the same
library Home Assistant's modbus integration uses), so anything verified here
maps directly to a HA sensor definition.

The register map lives in ctc_registers.py, generated from the official BMS
manual. Registers are read with FC03 at their documented address (offset 0).

The BMS supports up to 10 heat pumps (HP1..HP10) and repeats every per-pump
field 10 times. Most installations have one, so everything defaults to HP1;
use --hp N to look at another.

The controller's address is site-specific, so there is no default: pass --host,
or export CTC_HOST=<address> once and omit it.

Usage:
    python ctc_modbus_test.py verify            # read all registers for HP1
    python ctc_modbus_test.py --hp 2 verify     # ... for heat pump 2
    python ctc_modbus_test.py verify --system   # system registers only
    python ctc_modbus_test.py verify --setpoints  # include writable setpoints
    python ctc_modbus_test.py read 62027        # read one register
    python ctc_modbus_test.py read tempin       # ... by (partial) name
    python ctc_modbus_test.py poll 62017 -i 5   # re-read every 5 seconds
    python ctc_modbus_test.py scan 62000 62130  # sweep a raw address range
    python ctc_modbus_test.py ha 62027          # print Home Assistant YAML
    python ctc_modbus_test.py list              # list known registers

Requires: pip install pymodbus   (tested with pymodbus 3.14, API >= 3.13)
"""

import argparse
import os
import sys
import time
from pathlib import Path

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

# The register map lives inside the HA component (the one canonical, generated
# copy). Import it directly off that directory so this CLI needs no HA install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "custom_components" / "ctc_bms"))
from registers import (MAX_HEAT_PUMPS, MAX_ZONES, SYSTEM_REGISTERS, Reg,
                       all_registers, device_registers, registers_for_hp,
                       registers_for_zone)

# ---------------------------------------------------------------------------
# Defaults - CTC "Set. BMS" screen
# ---------------------------------------------------------------------------
# No default host: the controller's address is site-specific. Pass --host, or
# set CTC_HOST once in your shell to avoid typing it every time.
DEFAULT_HOST = os.environ.get("CTC_HOST") or None
DEFAULT_PORT = 502
DEFAULT_DEVICE_ID = 1        # "MB Address: 1"
# (Baudrate 9600 / parity E / 1 stop bit on that same screen are the RS-485
#  side of the controller - they don't apply to the TCP connection.)

# The manual caps a transfer at 100 registers. A block read costs the same ~10ms
# as a single register, so reading in blocks is what keeps verify/scan fast.
BLOCK = 100

# An unfitted sensor reports -9999/-10000 rather than an error.
SENTINELS = {55536, 55537}   # -10000, -9999


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

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
    return val * reg.scale


def format_value(reg: Reg, words: list[int]) -> str:
    if words[0] in SENTINELS and reg.count == 1:
        return "no sensor"
    return f"{decode_value(reg, words):g} {reg.unit}".strip()


# ---------------------------------------------------------------------------
# Modbus
# ---------------------------------------------------------------------------

def read_block(client, address: int, device_id: int, count: int):
    """Read `count` holding registers (FC03). Returns words, or None.

    None means at least one address in the range does not exist: this controller
    silently drops such requests instead of returning IllegalDataAddress, so a
    nonexistent register is indistinguishable from a dead link at this level.
    """
    try:
        rr = client.read_holding_registers(address, count=count,
                                           device_id=device_id)
        if rr.isError():
            return None
        return rr.registers
    except ModbusException:
        return None


def read_addresses(client, addrs: set[int], device_id: int) -> dict[int, int]:
    """Fetch many addresses efficiently. Missing keys = register doesn't exist.

    Reads in blocks; when a block comes back silent (one of its addresses is
    absent) it is split in half rather than abandoned, so one missing register
    doesn't cost us the other 99.
    """
    found: dict[int, int] = {}
    if not addrs:
        return found

    def fetch(lo: int, hi: int):
        span = hi - lo + 1
        words = read_block(client, lo, device_id, span)
        if words is not None:
            for i, w in enumerate(words):
                found[lo + i] = w
            return
        if span == 1:
            return                      # this single address is dead
        mid = lo + span // 2
        fetch(lo, mid - 1)
        fetch(mid, hi)

    # Only read spans that actually contain wanted addresses. Walking the whole
    # min..max range instead would bisect its way through every dead gap in
    # between, which is ruinously slow (each dead address costs a full timeout).
    ordered = sorted(addrs)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] - ordered[i] < BLOCK:
            j += 1
        fetch(ordered[i], ordered[j])
        i = j + 1
    return found


def run_length(client, start: int, device_id: int, cap: int) -> int:
    """Largest L in 1..cap such that [start, start+L) all exist; 0 if start dead."""
    if read_block(client, start, device_id, 1) is None:
        return 0
    lo, hi = 1, cap
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if read_block(client, start, device_id, mid) is not None:
            lo = mid
        else:
            hi = mid - 1
    return lo


def next_live(client, start: int, end: int, device_id: int, stride: int) -> int:
    """First live address at or after `start`, else end+1.

    Dead addresses each cost a timeout, so we sample every `stride` rather than
    walking one at a time, then back up to the true start of the run we land in.
    A live run shorter than `stride` can be missed.
    """
    addr = start
    while addr <= end:
        if read_block(client, addr, device_id, 1) is not None:
            while addr > start and read_block(client, addr - 1, device_id, 1):
                addr -= 1
            return addr
        addr += stride
    return end + 1


def connect(args) -> ModbusTcpClient:
    # The only place a host is needed, so `list` and `ha` work offline.
    if not args.host:
        sys.exit("No controller address: pass --host, or set CTC_HOST.")
    # pymodbus retries 3x by default, which triples the cost of every dead
    # address - and reading a full register map hits plenty of those.
    client = ModbusTcpClient(args.host, port=args.port, timeout=args.timeout,
                             retries=args.retries)
    if not client.connect():
        sys.exit(f"Could not connect to {args.host}:{args.port} - check the pump "
                 f"is reachable and Modbus TCP is enabled.")
    print(f"Connected to {args.host}:{args.port} (device id {args.device_id})")
    return client


# ---------------------------------------------------------------------------
# Register selection
# ---------------------------------------------------------------------------

def selected_registers(args) -> list[Reg]:
    if getattr(args, "system", False):
        regs = sorted(SYSTEM_REGISTERS, key=lambda r: r.number)
    elif getattr(args, "hp_only", False):
        regs = registers_for_hp(args.hp)
    elif getattr(args, "zone_only", False):
        regs = registers_for_zone(args.zone)
    else:
        regs = all_registers(args.hp, args.zone)
    if not getattr(args, "setpoints", False):
        regs = [r for r in regs if r.access == "R"]
    return regs


def find_register(token: str, hp: int, zone: int) -> Reg | None:
    """Look up a register by number or by (partial, case-insensitive) name."""
    pool = all_registers(hp, zone)
    if token.isdigit():
        n = int(token)
        for r in pool:
            if r.number == n:
                return r
        return Reg(n, f"Unknown register {n}", "not in the BMS manual")
    matches = [r for r in pool if token.lower() in r.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        print(f"'{token}' matches {len(matches)} registers:")
        for r in matches[:20]:
            print(f"  {r.number:>6}  {r.name}")
        return None
    print(f"No register matches '{token}'. Try a number, or 'list'.")
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_verify(args):
    regs = selected_registers(args)
    client = connect(args)
    t0 = time.time()
    try:
        wanted = {r.number + i for r in regs for i in range(r.count)}
        words = read_addresses(client, wanted, args.device_id)
    finally:
        client.close()

    print(f"\nHeat pump {args.hp} of {MAX_HEAT_PUMPS}  |  {len(regs)} registers"
          f"{' (incl. setpoints)' if args.setpoints else ''}\n")
    print(f"{'Reg':>6}  {'A':<2} {'Name':<28} {'Value':<14} Description")
    print("-" * 100)
    absent = 0
    for r in regs:
        got = [words.get(r.number + i) for i in range(r.count)]
        if any(w is None for w in got):
            absent += 1
            if not args.all:
                continue
            cell = "-"
        else:
            # A 0 is kept, not hidden: an idle compressor genuinely reads 0 and
            # that is a meaningful reading, unlike an absent register.
            cell = format_value(r, got)
        print(f"{r.number:>6}  {r.access:<2} {r.name[:28]:<28} {cell:<14} "
              f"{r.desc[:44]}")
    print(f"\n{len(regs) - absent} present, {absent} not implemented on this unit "
          f"({time.time() - t0:.1f}s)")
    return 0


def cmd_devices(args):
    """Which Home Assistant devices are worth creating for this installation.

    Every HP1..HP10 and Zone1..Zone4 register exists on the wire whether or not
    the hardware is fitted - absent ones just read 0. So "is this pump real?" is
    answered by whether ANY of its registers is nonzero, not by whether the
    registers respond.
    """
    devices = device_registers()
    if not args.setpoints:
        devices = {d: [r for r in rs if r.access == "R"]
                   for d, rs in devices.items()}
    client = connect(args)
    try:
        wanted = {r.number + i
                  for rs in devices.values() for r in rs for i in range(r.count)}
        words = read_addresses(client, wanted, args.device_id)
    finally:
        client.close()

    print(f"\n{'Device':<8} {'Regs':>5} {'Data':>5} {'NoSensor':>9}  Verdict")
    print("-" * 62)
    for dev, rs in devices.items():
        data = no_sensor = 0
        for r in rs:
            got = [words.get(r.number + i) for i in range(r.count)]
            if any(w is None for w in got):
                continue
            # -9999/-10000 is the "no sensor fitted" sentinel. It is numerically
            # nonzero, so counting raw nonzeros marks an EMPTY zone as present -
            # the sentinel is evidence of absence, not presence.
            if r.count == 1 and got[0] in SENTINELS:
                no_sensor += 1
            elif any(w != 0 for w in got):
                data += 1
        if dev == "System":
            verdict = "always create"
        elif data == 0:
            verdict = "not fitted - skip"
        elif data == 1:
            verdict = "maybe - only 1 reading, check it"
        else:
            verdict = "PRESENT - create"
        print(f"{dev:<8} {len(rs):>5} {data:>5} {no_sensor:>9}  {verdict}")
    print("\n'Data' counts registers with a real nonzero reading; 'NoSensor' counts the"
          "\n-9999/-10000 sentinel, which means the sensor is not fitted. A device with"
          "\nno data is not installed. Export one with:"
          "\n  ctc_modbus_test.py ha --device HP1")
    return 0


def cmd_read(args):
    reg = find_register(args.register, args.hp, args.zone)
    if reg is None:
        return 1
    client = connect(args)
    try:
        words = read_block(client, reg.number, args.device_id, reg.count)
    finally:
        client.close()
    print(f"\n=== {reg.number}: {reg.name} ===")
    if reg.desc:
        print(f"    {reg.desc}")
    if words is None:
        print("    no response - this register does not exist on this unit")
        return 1
    raws = " ".join(f"{w}(0x{w:04X})" for w in words)
    print(f"    raw={raws}  {reg.dtype} -> {format_value(reg, words)}")
    return 0


def cmd_poll(args):
    reg = find_register(args.register, args.hp, args.zone)
    if reg is None:
        return 1
    client = connect(args)
    print(f"Polling {reg.number} ({reg.name}) every {args.interval}s - Ctrl+C to stop")
    try:
        while True:
            words = read_block(client, reg.number, args.device_id, reg.count)
            stamp = time.strftime("%H:%M:%S")
            if words is None:
                print(f"[{stamp}] no response")
            else:
                print(f"[{stamp}] {format_value(reg, words)}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.close()
    return 0


def cmd_scan(args):
    """Sweep a raw address range - for exploring beyond the documented map."""
    by_number = {r.number: r for r in all_registers(args.hp)}
    client = connect(args)
    found = 0
    try:
        print(f"\nScanning {args.start}..{args.end} (FC03, blocks of {BLOCK}, "
              f"stride {args.stride} over gaps); "
              f"{'all' if args.all else 'nonzero only'}.\n")
        print(f"{'Reg':>6}  {'raw':>6}  {'s16':>7}  {'x0.1':>8}")
        print("-" * 40)
        addr = args.start
        while addr <= args.end:
            span = min(BLOCK, args.end - addr + 1)
            words = read_block(client, addr, args.device_id, span)
            if words is None:
                length = run_length(client, addr, args.device_id, span)
                if length == 0:
                    addr = next_live(client, addr + 1, args.end,
                                     args.device_id, args.stride)
                    continue
                words = read_block(client, addr, args.device_id, length)
                if words is None:
                    addr += length
                    continue
            for i, raw in enumerate(words):
                if raw == 0 and not args.all:
                    continue
                s = to_signed16(raw)
                known = by_number.get(addr + i)
                note = f"  <- {known.name}" if known else ""
                if raw in SENTINELS:
                    note += "  (sensor not connected)"
                print(f"{addr + i:>6}  {raw:>6}  {s:>7}  {s / 10:>8.1f}{note}")
                found += 1
            addr += len(words)
    finally:
        client.close()
    print(f"\n{found} registers shown.")
    return 0


def ha_sensor(reg: Reg, device_id: int) -> str:
    """One HA modbus sensor block. `device` is not an HA modbus key - grouping
    into devices is done by the entity naming convention (CTC HP1 ...), which HA
    turns into a device when combined with a `device_class`/integration, so keep
    the prefix stable."""
    slug = "".join(c if c.isalnum() else "_" for c in reg.name.lower()).strip("_")
    data_type = {"S16": "int16", "U16": "uint16",
                 "S32": "int32", "U32": "uint32"}[reg.dtype]
    out = [f'      - name: "CTC {reg.name}"',
           f"        unique_id: ctc_{slug}",
           f"        slave: {device_id}",
           f"        address: {reg.number}",
           "        input_type: holding",
           f"        data_type: {data_type}",
           f"        count: {reg.count}",
           f"        scale: {reg.scale}",
           "        precision: 1"]
    if reg.unit:
        out.append(f'        unit_of_measurement: "{reg.unit}"')
    if reg.count == 2:
        out.append("        swap: word")      # CTC 32-bit values are LSB-first
    if reg.unit == "°C":
        out += ["        device_class: temperature",
                "        state_class: measurement"]
    return "\n".join(out)


def cmd_ha(args):
    if args.device:
        devices = device_registers()
        key = next((d for d in devices if d.lower() == args.device.lower()), None)
        if key is None:
            print(f"Unknown device '{args.device}'. "
                  f"Try: {', '.join(list(devices)[:6])}, ...")
            return 1
        regs = devices[key]
        if not args.setpoints:
            regs = [r for r in regs if r.access == "R"]
        header = f"# CTC {key}: {len(regs)} sensors"
    elif args.register:
        reg = find_register(args.register, args.hp, args.zone)
        if reg is None:
            return 1
        regs = [reg]
        header = f"# CTC {reg.name}"
    else:
        print("give a register, or --device HP1 / Zone1 / System")
        return 1

    body = "\n".join(ha_sensor(r, args.device_id) for r in regs)
    print(f"""
{header}
# Home Assistant configuration.yaml (classic modbus integration).
# 32-bit CTC values are LSB-first, hence `swap: word`.
modbus:
  - name: ctc_heatpump
    type: tcp
    host: {args.host}
    port: {args.port}
    sensors:
{body}
""")
    return 0


def cmd_list(args):
    regs = selected_registers(args)
    print(f"{'Reg':>6}  {'A':<2} {'Type':<4} {'Scale':<5} {'Unit':<4} "
          f"{'Name':<28} Description")
    print("-" * 108)
    for r in regs:
        print(f"{r.number:>6}  {r.access:<2} {r.dtype:<4} {r.scale:<5g} "
              f"{r.unit:<4} {r.name[:28]:<28} {r.desc[:40]}")
    print(f"\n{len(regs)} registers (heat pump {args.hp}). "
          f"Add --setpoints for writable ones, --system for system-only.")
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=DEFAULT_HOST,
                   help="controller address (or set CTC_HOST); "
                        "not needed by `list` or `ha`")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID,
                   help="Modbus unit/slave id (MB Address)")
    p.add_argument("--hp", type=int, default=1, choices=range(1, MAX_HEAT_PUMPS + 1),
                   metavar="N", help=f"which heat pump, 1..{MAX_HEAT_PUMPS} (default 1)")
    p.add_argument("--zone", type=int, default=1, choices=range(1, MAX_ZONES + 1),
                   metavar="N", help=f"which heating system/zone, 1..{MAX_ZONES} "
                                     f"(default 1)")
    p.add_argument("--timeout", type=float, default=1.0)
    p.add_argument("--retries", type=int, default=1,
                   help="pymodbus retries; a dead register costs timeout x retries")

    sub = p.add_subparsers(dest="command", required=True)

    def add_scope(sp):
        sp.add_argument("--system", action="store_true",
                        help="system registers only")
        sp.add_argument("--hp-only", action="store_true", dest="hp_only",
                        help="only this heat pump's registers")
        sp.add_argument("--zone-only", action="store_true", dest="zone_only",
                        help="only this zone's registers")
        sp.add_argument("--setpoints", action="store_true",
                        help="include writable (RW) setpoints; never written")

    sp = sub.add_parser("verify", help="read the register map off the pump")
    add_scope(sp)
    sp.add_argument("--all", action="store_true",
                    help="also list registers this unit doesn't implement")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("devices", help="which HA devices this installation has")
    sp.add_argument("--setpoints", action="store_true")
    sp.set_defaults(func=cmd_devices)

    sp = sub.add_parser("list", help="list known registers (no network)")
    add_scope(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("read", help="read one register")
    sp.add_argument("register", help="register number or (partial) name")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("poll", help="repeatedly read one register")
    sp.add_argument("register")
    sp.add_argument("-i", "--interval", type=float, default=5.0)
    sp.set_defaults(func=cmd_poll)

    sp = sub.add_parser("scan", help="sweep a raw address range")
    sp.add_argument("start", type=int)
    sp.add_argument("end", type=int)
    sp.add_argument("--all", action="store_true", help="include registers reading 0")
    sp.add_argument("--stride", type=int, default=16,
                    help="sampling step when skipping dead space (default 16)")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("ha", help="print Home Assistant YAML for a register or device")
    sp.add_argument("register", nargs="?",
                    help="register number or name (omit if using --device)")
    sp.add_argument("--device", metavar="NAME",
                    help="emit every sensor for a device: System, HP1..HP10, Zone1..Zone4")
    sp.add_argument("--setpoints", action="store_true",
                    help="include writable (RW) registers in a --device export")
    sp.set_defaults(func=cmd_ha)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
