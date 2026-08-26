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

Two commands WRITE, and only ever to the 1000-1100 "control parameters" - which
the manual says carry no write-cycle cost and are discarded by the controller
after 5 minutes. Both need --yes:

    python ctc_modbus_test.py control 1100 64 --hold 60 --yes   # close DI6
    python ctc_modbus_test.py discover-di --quick --yes         # what is each bit?

Nothing here ever writes a 61500-range register: those are stored parameters
with a limited write-cycle count.

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


def write_register(client, address: int, value: int, device_id: int):
    """Write one holding register with FC16. Returns (ok, detail).

    The manual specifies FC16 even for a single register, which is what the HA
    integration does too. Only ever called on the 1000-range control registers
    by this tool - see the warning in cmd_discover_di.
    """
    try:
        rr = client.write_registers(address, [value], device_id=device_id)
        if rr.isError():
            return False, f"controller rejected the write ({rr})"
        return True, ""
    except ModbusException as err:
        return False, str(err)


def hold_write(client, address: int, value: int, device_id: int,
                seconds: float, refresh: float = 60.0) -> bool:
    """Keep a control register asserted for `seconds`.

    The 1000-range registers are discarded by the controller if they are not
    re-written within 5 minutes, so anything observed for longer than that has
    to be refreshed. 60 s gives four refreshes per window - the same
    belt-and-braces margin the integration's keepalive uses.
    """
    deadline = time.time() + seconds
    ok, detail = write_register(client, address, value, device_id)
    if not ok:
        print(f"  write {address}={value} failed: {detail}")
        return False
    while time.time() < deadline:
        time.sleep(min(refresh, max(deadline - time.time(), 0)))
        if time.time() >= deadline:
            break
        write_register(client, address, value, device_id)
    return True


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


# ---------------------------------------------------------------------------
# Control registers (1000-1100)
# ---------------------------------------------------------------------------
#
# A different family from everything above: write-only, no read-back, no
# write-cycle cost, and DISCARDED BY THE CONTROLLER AFTER 5 MINUTES unless
# re-written. Register 1100 is the virtual digital inputs, which the manual says
# stand in for terminals K22-K24 - bits 0..7 = DI0..DI7, 0 = open, 1 = closed.

VDI_REGISTER = 1100
CONTROL_RANGE = range(1000, 1101)

# Registers worth watching while a virtual input is closed: the documented
# read-backs for SmartGrid, DHW circulation and the zone/DHW states. --quick
# narrows the sweep to these; the default watches every system R register,
# because the point of discovery is finding read-backs we did NOT predict.
QUICK_WATCH = [
    62301,                      # SGMode - the SmartGrid read-back
    62016,                      # sDHWPump "DHW circulation"
    62005,                      # sStatus - what the installation is doing
    62001, 62002, 62003,        # DHW stop temp / outlet setpoint / temperature
    62246, 62247, 62248, 62249,  # zone status
    62322,                      # active cooling demand
    62280,                      # exhaust fan percent
    62365,                      # periodic extra DHW status
    62017,                      # HP1 status
]


def _watch_set(args) -> list[int]:
    if args.watch:
        return sorted({int(t) for t in args.watch.replace(",", " ").split()})
    if args.quick:
        return sorted(QUICK_WATCH)
    # Everything readable, not just the system registers: a digital input could
    # plausibly drive a zone or heat-pump state, and block reads make the extra
    # coverage free. (read_addresses returns every address inside each span it
    # reads, so the set actually compared is wider still - it is reported.)
    return sorted(r.number for r in all_registers(args.hp, args.zone)
                  if r.access == "R" and r.count == 1)


def _describe(number: int, by_number: dict[int, Reg]) -> str:
    reg = by_number.get(number)
    return f"{reg.name} - {reg.desc[:40]}" if reg else "(not in the map)"


def cmd_control(args):
    """Write a 1000-range control register, optionally holding it asserted."""
    if args.number not in CONTROL_RANGE:
        sys.exit(f"{args.number} is not a control register (1000-1100). This "
                 f"tool will not write anything else - the 61500-range "
                 f"registers are stored parameters with a limited write-cycle "
                 f"count.")
    if not args.yes:
        sys.exit("This writes to a live heating system. Re-run with --yes.")
    client = connect(args)
    try:
        if args.hold:
            print(f"Holding {args.number} = {args.value} for {args.hold}s "
                  f"(re-writing every 60s; the controller discards it after "
                  f"300s without one). Ctrl+C to stop.")
            hold_write(client, args.number, args.value, args.device_id,
                       args.hold)
            print("Done - the value now expires on the controller's own "
                  "5-minute timer.")
        else:
            ok, detail = write_register(client, args.number, args.value,
                                        args.device_id)
            print(f"{args.number} = {args.value}: "
                  f"{'written' if ok else 'FAILED - ' + detail}")
            if ok:
                print("Note: write-only, so this cannot be read back, and the "
                      "controller discards it within 5 minutes.")
            return 0 if ok else 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        client.close()
    return 0


def _baseline(client, watch, args):
    """Read the watch set twice and return (values, drifting addresses).

    Anything moving on its own - temperatures, degree minutes, run counters -
    is masked out of every later comparison, or the results are thermal noise.
    """
    print("Baseline...")
    time.sleep(args.settle)
    first = read_addresses(client, set(watch), args.device_id)
    time.sleep(args.settle)
    base = read_addresses(client, set(watch), args.device_id)
    drift = {a for a in base if first.get(a) != base.get(a)}
    print(f"  comparing {len(base)} addresses "
          f"(block reads return more than the {len(watch)} asked for)"
          + (f"; ignoring {len(drift)} that drift on their own" if drift else ""))
    return base, drift


def cmd_probe(args):
    """Write a control register two different values and report what moved.

    Answers the two questions a write-only register cannot answer itself: did
    the controller act on it, and what scale is it using? Two values rather than
    one because a read-back that *tracks* both is causal, where a single change
    could be coincidence - and the pair of readings is what reveals the scale.

    There is no "release" for a setpoint: writing 0 would be a command, not a
    release, so the probe simply stops refreshing and lets the controller's own
    5-minute expiry undo it.
    """
    if args.number not in CONTROL_RANGE:
        sys.exit(f"{args.number} is not a control register (1000-1100).")
    if not args.yes:
        sys.exit(
            f"probe WRITES to a live heating system.\n\n"
            f"It sets register {args.number} to {args.first}, then {args.second}, "
            f"holding each for {args.dwell:g}s, and reports which registers "
            f"followed.\nBoth values are discarded by the controller within 5 "
            f"minutes of the last write.\nDisable the Home Assistant config "
            f"entry first - the controller cannot pipeline.\n\n"
            f"Re-run with --yes."
        )

    watch = _watch_set(args)
    by_number = {r.number: r for r in all_registers(args.hp, args.zone)}
    client = connect(args)
    print(f"\nProbing {args.number} with {args.first} then {args.second}, "
          f"holding each {args.dwell:g}s.\n")
    try:
        base, drift = _baseline(client, watch, args)

        phases = []
        for value in (args.first, args.second):
            print(f"  {args.number} = {value} ... holding {args.dwell:g}s")
            if not hold_write(client, args.number, value, args.device_id,
                              args.dwell):
                sys.exit(f"Could not write {args.number}.")
            phases.append(read_addresses(client, set(watch), args.device_id))

        moved = sorted(
            a for a in base
            if a not in drift
            and (phases[0].get(a) != base[a] or phases[1].get(a) != base[a])
        )
        print("\n" + "=" * 78)
        if not moved:
            print(f"Nothing responded to register {args.number}.\n\n"
                  f"Either this controller does not implement it, or the "
                  f"function behind it\nis not configured/enabled in the "
                  f"controller's own menus. Note that this\ncontroller accepts "
                  f"writes it does not act on, so the successful write\nabove "
                  f"is not evidence either way.")
        else:
            print(f"{'Reg':>6} {'base':>7} {'@' + str(args.first):>8} "
                  f"{'@' + str(args.second):>8}   Name / decoded")
            print("-" * 78)
            for a in moved:
                reg = by_number.get(a)
                b, p1, p2 = base[a], phases[0].get(a), phases[1].get(a)
                print(f"{a:>6} {b:>7} {p1:>8} {p2:>8}   "
                      f"{reg.name if reg else '(not in the map)'}")
                if reg:
                    dec = [format_value(reg, [v]) for v in (b, p1, p2)]
                    print(f"{'':>6} {'':>7} {'':>8} {'':>8}   "
                          f"decoded: {dec[0]} -> {dec[1]} -> {dec[2]}")
            tracking = [a for a in moved
                        if phases[0].get(a) == args.first
                        and phases[1].get(a) == args.second]
            if tracking:
                print(f"\n{', '.join(str(a) for a in tracking)} copied the "
                      f"written word exactly, so {args.number} and that "
                      f"read-back share a scale.")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        client.close()
    print(f"\nStopped writing. The controller discards {args.number} within 5 "
          f"minutes\nand reverts to its own stored setting - nothing to undo.")
    return 0


def cmd_discover_di(args):
    """Find which virtual digital input bit does what, empirically.

    The manual is explicit that a terminal's DI number is configured in the
    controller's own menus, so it cannot be looked up - only observed. This
    closes one bit at a time and reports which registers moved *and moved back*,
    which is what separates a real effect from thermal drift.
    """
    if not args.yes:
        print(__doc__.split("Usage:")[0])
        sys.exit(
            "discover-di WRITES to a live heating system.\n"
            "\n"
            "It closes each virtual digital input in turn. Depending on how the\n"
            "inputs are configured on your controller that can block the heat\n"
            "pump, start hot water circulation, force SmartGrid states or change\n"
            "zone modes. Each state is held for the dwell time and then released.\n"
            "\n"
            "Before running:\n"
            "  * disable the Home Assistant config entry - the controller cannot\n"
            "    pipeline, and two masters writing 1100 produce nonsense;\n"
            "  * do not run it while anyone needs hot water or heating;\n"
            "  * know that everything written here self-expires within 5 minutes.\n"
            "\n"
            "Re-run with --yes when that is all true."
        )

    bits = [int(b) for b in args.bits.replace(",", " ").split()]
    if any(b < 0 or b > 7 for b in bits):
        sys.exit("bits must be 0-7")
    watch = _watch_set(args)
    by_number = {r.number: r for r in all_registers(args.hp, args.zone)}
    est = len(bits) * (args.dwell + 2 * args.settle) / 60.0

    client = connect(args)
    print(f"\nWatching {len(watch)} registers, {len(bits)} bits, "
          f"dwell {args.dwell}s, settle {args.settle}s "
          f"-> about {est:.0f} minutes.\n")
    try:
        ok, detail = write_register(client, VDI_REGISTER, 0, args.device_id)
        if not ok:
            sys.exit(f"Cannot write register {VDI_REGISTER}: {detail}\n"
                     f"This controller may not support virtual digital inputs.")

        base, drift = _baseline(client, watch, args)

        findings: dict[int, list[tuple[int, int, int]]] = {}
        for bit in bits:
            mask = 1 << bit
            print(f"\nDI{bit} (1100 = {mask})... closing for {args.dwell}s")
            if not hold_write(client, VDI_REGISTER, mask, args.device_id,
                              args.dwell):
                continue
            closed = read_addresses(client, set(watch), args.device_id)

            changed = {a: (base[a], closed[a]) for a in closed
                       if a in base and a not in drift
                       and closed[a] != base[a]}

            write_register(client, VDI_REGISTER, 0, args.device_id)
            time.sleep(args.settle)
            after = read_addresses(client, set(watch), args.device_id)

            # A change that does not revert was drift we failed to catch in the
            # baseline, not this bit.
            hits = [(a, was, now) for a, (was, now) in sorted(changed.items())
                    if after.get(a) == was]
            stuck = [a for a in changed if after.get(a) != changed[a][0]]
            findings[bit] = hits
            if hits:
                for a, was, now in hits:
                    print(f"    {a:>6}  {was:>6} -> {now:<6}  "
                          f"{_describe(a, by_number)}")
            else:
                print("    no effect")
            if stuck:
                print(f"    ({len(stuck)} changed but did not revert - "
                      f"treated as drift)")

        print("\n" + "=" * 72)
        print("Summary")
        print("=" * 72)
        for bit in bits:
            hits = findings.get(bit) or []
            if not hits:
                print(f"  DI{bit}: nothing")
                continue
            names = ", ".join(f"{a} ({by_number[a].name})" if a in by_number
                              else str(a) for a, _, _ in hits)
            print(f"  DI{bit}: {names}")
        print("\nA bit that moved 62016 (DHW circulation) is your K22 hot water"
              "\ncirculation trigger. A bit that moved 62301 (SGMode) is a "
              "SmartGrid\ninput - confirm the pair with --sg-pair A,B.")

        if args.sg_pair:
            a, b = (int(x) for x in args.sg_pair.replace(",", " ").split())
            print(f"\nConfirming SmartGrid on DI{a} (A) / DI{b} (B) against "
                  f"62301...")
            # The manual's truth table: A open/B closed = Low price;
            # both closed = Overcapacity; A closed/B open = Blocking.
            expect = {
                (0, 1): (2, "Low price"),
                (1, 1): (3, "High capacity / overcapacity"),
                (1, 0): (1, "Block"),
                (0, 0): (0, "None / normal"),
            }
            for (ca, cb), (want, label) in expect.items():
                mask = (ca << a) | (cb << b)
                hold_write(client, VDI_REGISTER, mask, args.device_id,
                           args.settle)
                got = read_addresses(client, {62301}, args.device_id).get(62301)
                verdict = "OK" if got == want else f"MISMATCH (wanted {want})"
                print(f"    A={'closed' if ca else 'open':<6} "
                      f"B={'closed' if cb else 'open':<6} "
                      f"1100={mask:<4} 62301={got}  {label:<28} {verdict}")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        # Never leave an input closed. It would have expired within 5 minutes
        # anyway, but do not make the user wait it out.
        write_register(client, VDI_REGISTER, 0, args.device_id)
        print(f"\nReleased: {VDI_REGISTER} = 0.")
        client.close()
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

    sp = sub.add_parser("control",
                        help="write a 1000-range control register (WRITES!)")
    sp.add_argument("number", type=int, help="1000-1100 only")
    sp.add_argument("value", type=int)
    sp.add_argument("--hold", type=float, metavar="SECONDS",
                    help="keep re-writing it for this long (it expires after "
                         "300s without a refresh)")
    sp.add_argument("--yes", action="store_true",
                    help="confirm you mean to write to a live heating system")
    sp.set_defaults(func=cmd_control)

    sp = sub.add_parser("probe",
                        help="write a control register two values and report "
                             "what followed (WRITES!)")
    sp.add_argument("number", type=int, help="1000-1100 only")
    sp.add_argument("first", type=int, help="first value to try")
    sp.add_argument("second", type=int, help="second value, to confirm tracking")
    sp.add_argument("--dwell", type=float, default=30.0,
                    help="seconds to hold each value (default 30)")
    sp.add_argument("--settle", type=float, default=10.0)
    sp.add_argument("--watch", help="comma-separated registers to watch "
                                    "(default: everything readable)")
    sp.add_argument("--quick", action="store_true",
                    help="watch only the documented read-backs")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("discover-di",
                        help="find what each virtual digital input does (WRITES!)")
    sp.add_argument("--bits", default="0,1,2,3,4,5,6,7",
                    help="which bits to walk (default all 8)")
    sp.add_argument("--dwell", type=float, default=30.0,
                    help="seconds to hold each bit closed (default 30)")
    sp.add_argument("--settle", type=float, default=10.0,
                    help="seconds to wait before reading (default 10)")
    sp.add_argument("--watch", help="comma-separated registers to watch "
                                    "(default: every system read-only register)")
    sp.add_argument("--quick", action="store_true",
                    help="watch only the documented SmartGrid/DHW read-backs")
    sp.add_argument("--sg-pair", metavar="A,B",
                    help="after the walk, confirm these two bits are SmartGrid "
                         "A/B against 62301")
    sp.add_argument("--yes", action="store_true",
                    help="confirm you mean to write to a live heating system")
    sp.set_defaults(func=cmd_discover_di)

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
