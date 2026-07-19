#!/usr/bin/env python3
"""Generate custom_components/ctc_bms/registers.py (a hardcoded Python register
map) from the parsed BMS manual. Re-run after parse_bms.py; do not hand-edit the
generated file.

The manual is full of ARRAYS: every heat-pump field is repeated 10 times (HP1..
HP10) and every heating-system field 4 times (zones 1..4), consecutively. We
detect those and emit one template per field instead of N flat registers, so the
tool can address "HP3's suction gas" arithmetically.

Detection has to look at BOTH the Name column and the description, because the
manual leaves Name blank for some array rows ("HP3 Power consumption kW",
"Heating system 3: Shunt state") - keying off Name alone silently drops them into
the system list, where they masquerade as N unrelated registers.
"""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "dev" / "bms_registers.json"
OUT = REPO / "custom_components" / "ctc_bms" / "registers.py"

regs = {int(k): v for k, v in json.load(open(SRC)).items()}


class Family:
    """One array family: N members, detected by name pattern and/or description."""

    def __init__(self, kind, size, name_re, desc_re, overrides):
        self.kind = kind              # 'HP' | 'Zone'
        self.size = size
        self.name_re = re.compile(name_re)
        self.desc_re = re.compile(desc_re, re.I)
        self.overrides = overrides

    def match(self, name, desc):
        """(index, field) if this row belongs to the family, else None."""
        m = self.name_re.match(name)
        if m:
            return int(m.group(1)), m.group(2)
        m = self.desc_re.match(desc.strip())
        if m:
            rest = re.sub(r"\s+", " ", m.group(2)).strip().lower()
            return int(m.group(1)), self.overrides.get(rest) or camel(rest)
        return None


def camel(s: str) -> str:
    """'shunt state 0 = close, 1 = open' -> 'ShuntState'."""
    s = re.split(r"\d\s*=", s)[0]                     # drop enum legends
    words = re.findall(r"[a-zA-Z]+", s)[:3]
    return "".join(w.capitalize() for w in words) or "Unnamed"


FAMILIES = [
    Family("HP", 10,
           r"^hp(\d+)([A-Z]\w*)$",
           r"^(?:heat\s*pump\s*(\d+)\s*(?:\(a\d+\))?|hp\s*(\d+)|compressor\s*(\d+))\s*:?\s*(.*)$",
           {"primary system flow": "PrimarySystemFlow",
            "power consumption kw": "PowerConsumption",
            "power consumption kwh lsb": "Energy"}),
    Family("Zone", 4,
           r"^hs(\d+)([A-Z]\w*)$",
           r"^heating\s*system\s*(\d+)\s*:?\s*(.*)$",
           {}),
]


# The HP description regex has three alternative index groups; normalise so
# group(1) is always the index and the last group is always the remainder.
def family_match(fam, name, desc):
    m = fam.name_re.match(name)
    if m:
        return int(m.group(1)), m.group(2)
    m = fam.desc_re.match(desc.strip())
    if not m:
        return None
    groups = [g for g in m.groups() if g is not None]
    idx = next((g for g in groups if g.isdigit()), None)
    if idx is None:
        return None
    rest = re.sub(r"\s+", " ", groups[-1]).strip().lower()
    return int(idx), fam.overrides.get(rest) or camel(rest)


def slug(desc: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", desc).strip("_").lower()
    return re.sub(r"_+", "_", s)[:40] or "unnamed"


def unit_for(name: str, desc: str, factor: float) -> str:
    """Units are NOT in the manual - only a scale factor - so these are inferred.

    Infer from the identifier first: every array row's description begins "Heat
    pump 1 (A1): ..." / "Heating system 1: ...", so keyword-matching the
    description alone stamps "%" on temperatures and statuses.
    """
    n = name.lower()
    d = re.sub(r"^(heat pump|compressor|heating system)\s*\d*\s*(\([^)]*\))?\s*:?\s*",
               "", desc.strip().lower())

    if "kwh" in d or "kwh" in n:
        return "kWh"
    if re.search(r"\bkw\b", d):
        return "kW"
    if "pressure" in n or "pressure" in d:
        return "bar"
    if "rps" in n or "rps" in d:
        return "rps"
    if "degree minute" in d:
        return "DM"
    if "time" in n and "timer" not in n:
        return "h"
    if "operating time" in d or "total operation" in d:
        return "h"
    if re.search(r"\bdays?\b", d):
        return "days"
    if re.search(r"(pump|fan)$", n) or "percent" in d:
        return "%"
    # NOT "flow": "Primary system flow" never says temperature (unlike "Primary
    # flow temperature"), so it may be a flow rate - leave it unitless.
    if factor == 0.1 and (
        "temp" in n or "temp" in d
        or re.search(r"\b(return|radiator|brine|gas|boiler)\b", d)
    ):
        return "°C"
    return ""


def dtype_for(d):
    return ("S" if d["signed"] else "U") + ("32" if d["words"] == 2 else "16")


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ---- split registers into array families and everything else ---------------
found = {f.kind: {} for f in FAMILIES}
system = []
for a, d in sorted(regs.items()):
    for fam in FAMILIES:
        hit = family_match(fam, d["name"], d["description"])
        if hit:
            idx, field = hit
            found[fam.kind].setdefault(field, {})[idx] = a
            break
    else:
        system.append(a)

# Only promote a field to an array if the whole family is present with a
# consistent stride; anything else stays a plain system register.
templates = {f.kind: [] for f in FAMILIES}
for fam in FAMILIES:
    for field, idx in sorted(found[fam.kind].items(), key=lambda kv: kv[1].get(1, 0)):
        strides = {idx[i] - idx[i - 1] for i in sorted(idx) if i - 1 in idx}
        if len(idx) != fam.size or 1 not in idx or len(strides) != 1:
            system.extend(idx.values())
            continue
        stride = strides.pop()
        base = idx[1]
        assert all(idx[i] == base + (i - 1) * stride for i in idx), (fam.kind, field)
        templates[fam.kind].append((field, base, stride, regs[base]))
system.sort()

L = [
    '"""CTC BMS register map - GENERATED, do not hand-edit.',
    "",
    'Source: "User Manual-BMS Manual-16260016.pdf" (CTC, 2026-02-02), tables on',
    "pages 24-44. Regenerate with dev/scripts/parse_bms.py then",
    "dev/scripts/gen_registers.py.",
    "",
    "Protocol (from the manual): registers live above 49999, read with FC03, write",
    "with FC16, offset 0, max 100 registers per transfer.",
    "",
    "The map has three shapes, which is how you split it into Home Assistant",
    "devices: SYSTEM_REGISTERS (one controller), HP_FIELDS (x10 heat pumps) and",
    "ZONE_FIELDS (x4 heating systems). device_registers() does that split for you.",
    "",
    "dtype: S/U = signed/unsigned, 16/32 = width. A 32-bit value spans TWO registers",
    "stored LSB first, MSB second (value = MSB << 16 | LSB) - little-endian word",
    "order, the opposite of the usual Modbus convention.",
    "",
    "access: R = read-only, RW = writable setpoint. This tool never writes; RW rows",
    "are here so you can READ how the pump is configured.",
    "",
    "unit: INFERRED from the description - the manual gives only a scale factor - so",
    "treat units as a hint, not gospel.",
    '"""',
    "",
    "import re",
    "from dataclasses import dataclass",
    "",
    "",
    "@dataclass(frozen=True)",
    "class Reg:",
    "    number: int",
    "    name: str",
    "    desc: str = ''",
    "    dtype: str = 'S16'      # S16 | U16 | S32 | U32",
    "    scale: float = 0.1",
    "    unit: str = '°C'",
    "    access: str = 'R'       # R | RW",
    "    device: str = 'System'  # System | HP1..HP10 | Zone1..Zone4",
    "",
    "    @property",
    "    def count(self) -> int:",
    '        """Registers to read: 32-bit values span two."""',
    "        return 2 if self.dtype.endswith('32') else 1",
    "",
    "",
    "@dataclass(frozen=True)",
    "class ArrayField:",
    '    """A field repeated once per heat pump / zone: member n is at',
    "    base + (n-1)*stride.",
    '    """',
    "    field: str",
    "    base: int",
    "    stride: int",
    "    dtype: str",
    "    scale: float",
    "    unit: str",
    "    access: str",
    "    desc: str",
    "",
    "",
    "MAX_HEAT_PUMPS = 10",
    "MAX_ZONES = 4",
    "",
    "",
    "def _renumber(desc: str, n: int) -> str:",
    '    """The manual writes every array row out for member 1; renumber for n."""',
    "    desc = re.sub(r'Heat pump 1 \\(A1\\)', f'Heat pump {n} (A{n})', desc)",
    "    desc = re.sub(r'Heating system 1', f'Heating system {n}', desc)",
    "    desc = re.sub(r'\\bHP1\\b', f'HP{n}', desc)",
    "    desc = re.sub(r'Compressor 1\\b', f'Compressor {n}', desc)",
    "    return re.sub(r'(room temp) 1\\b', rf'\\1 {n}', desc)",
    "",
    "",
    "def _materialise(fields, n, label):",
    "    return [",
    "        Reg(f.base + (n - 1) * f.stride, f'{label} {f.field}',",
    "            _renumber(f.desc, n), f.dtype, f.scale, f.unit, f.access, label)",
    "        for f in fields",
    "    ]",
    "",
    "",
    "# --- per-heat-pump fields (HP1..HP10) --------------------------------------",
    "# HP4's brine-out is 62097 + 3 = 62100 - exactly the register the community HA",
    "# integration mislabels as 'heat pump status'. Real status is HP1 Status/62017.",
    "HP_FIELDS = [",
]

for field, base, stride, d in templates["HP"]:
    u = unit_for(field, d["description"], d["factor"])
    L.append(f"    ArrayField({field!r}, {base}, {stride}, {dtype_for(d)!r}, "
             f"{d['factor']}, {u!r}, {d['access']!r},\n"
             f'               "{esc(d["description"][:70])}"),')

L += [
    "]",
    "",
    "# --- per-heating-system fields (zones 1..4) --------------------------------",
    "ZONE_FIELDS = [",
]

for field, base, stride, d in templates["Zone"]:
    u = unit_for(field, d["description"], d["factor"])
    L.append(f"    ArrayField({field!r}, {base}, {stride}, {dtype_for(d)!r}, "
             f"{d['factor']}, {u!r}, {d['access']!r},\n"
             f'               "{esc(d["description"][:70])}"),')

L += [
    "]",
    "",
    "",
    "def registers_for_hp(n: int) -> list[Reg]:",
    '    """Registers for heat pump `n` (1-based)."""',
    "    if not 1 <= n <= MAX_HEAT_PUMPS:",
    "        raise ValueError(f'heat pump must be 1..{MAX_HEAT_PUMPS}, got {n}')",
    "    return _materialise(HP_FIELDS, n, f'HP{n}')",
    "",
    "",
    "def registers_for_zone(n: int) -> list[Reg]:",
    '    """Registers for heating system / zone `n` (1-based)."""',
    "    if not 1 <= n <= MAX_ZONES:",
    "        raise ValueError(f'zone must be 1..{MAX_ZONES}, got {n}')",
    "    return _materialise(ZONE_FIELDS, n, f'Zone{n}')",
    "",
    "",
    "# --- system-wide registers (neither per-pump nor per-zone) ------------------",
    "SYSTEM_REGISTERS = [",
]

for a in system:
    d = regs[a]
    name = d["name"] if d["name"] != "x" else slug(d["description"])
    u = unit_for(name, d["description"], d["factor"])
    L.append(f"    Reg({a}, {name!r}, \"{esc(d['description'][:70])}\",\n"
             f"        {dtype_for(d)!r}, {d['factor']}, {u!r}, {d['access']!r}, 'System'),")

L += [
    "]",
    "",
    "",
    "def device_registers(hps=range(1, MAX_HEAT_PUMPS + 1),",
    "                     zones=range(1, MAX_ZONES + 1)) -> dict[str, list[Reg]]:",
    '    """The map split into Home Assistant devices: System, HP1.., Zone1.., in',
    "    address order within each device.",
    '    """',
    "    devices = {'System': sorted(SYSTEM_REGISTERS, key=lambda r: r.number)}",
    "    for n in hps:",
    "        devices[f'HP{n}'] = registers_for_hp(n)",
    "    for n in zones:",
    "        devices[f'Zone{n}'] = registers_for_zone(n)",
    "    return devices",
    "",
    "",
    "def all_registers(hp: int = 1, zone: int = 1) -> list[Reg]:",
    '    """System registers plus one heat pump and one zone, in address order."""',
    "    regs = SYSTEM_REGISTERS + registers_for_hp(hp) + registers_for_zone(zone)",
    "    return sorted(regs, key=lambda r: r.number)",
    "",
]

open(OUT, "w").write("\n".join(L) + "\n")
print(f"wrote {OUT}")
print(f"  HP_FIELDS   {len(templates['HP']):>3} x {MAX_HP if (MAX_HP := 10) else 0} pumps")
print(f"  ZONE_FIELDS {len(templates['Zone']):>3} x 4 zones")
print(f"  SYSTEM      {len(system):>3}")
print(f"  total       {len(system) + len(templates['HP']) * 10 + len(templates['Zone']) * 4}")
