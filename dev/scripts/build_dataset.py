#!/usr/bin/env python3
"""Build ctc_dataset.json - one structured dataset describing WHAT a CTC install
can be (controllers, capabilities, options) and WHICH registers each part uses.

Inputs:
  bms_registers.json  - the register table parsed from the BMS manual
  ctc_registers.py    - the same table already split into system / HP x10 /
                        zone x4 arrays (that split is the hard part; reuse it)
  the two product brochures - hand-transcribed into CONTROLLERS below, because a
                        marketing PDF's capability matrix is a picture, not text
                        we can trust a parser with.

The dataset is meant to drive a picker: choose a controller, then it tells you
how many heat pumps and zones are legal and which options exist; the register
lists then say what to actually poll.

CRITICAL SEMANTIC: capability != register presence. Every HP1-HP10 and Zone1-
Zone4 register answers on a 2-heat-pump EcoLogic M - the absent ones just read 0.
So capability limits are a FILTER over a fixed register map, never a claim about
which addresses respond.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ctc_registers as R  # noqa: E402  (generated map; see module docstring)

RAW = json.loads((ROOT / "bms_registers.json").read_text())
OUT = ROOT / "ctc_dataset.json"


# --- controllers -------------------------------------------------------------
# Transcribed from the "Compatible with control of:" matrix, CTC EcoLogic II
# brochure (17008029-1, 2026-06-10) p3, and the EcoLogic S sheet (17003569_1,
# 2024-03-06). Anything the brochures do not state is null + flagged, never
# guessed - an invented limit here would silently truncate someone's register
# list.
CAPS = [
    "heat_pumps_1_2", "heat_pumps_3_10",
    "heating_systems_1_2", "heating_systems_3_4",
    "suppl_heat_0_10v", "suppl_heat_230v",
    "solar", "pool", "dhw_circulation", "ventilation",
    "cooling_passive_ground", "cooling_active_air", "cooling_active_ground",
    "dual_tank_sensors",
]

# M / L / XL columns, in CAPS order.
_M = [1, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0]
_L = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0]
_XL = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]

CONTROLLERS = [
    {
        "id": "ecologic_s_ea",
        "model": "CTC EcoLogic S EA",
        "series": "EcoLogic S",
        "ctc_no": "589560301",
        "description": "Single heat pump control for CTC EcoAir (air/water).",
        "max_heat_pumps": 1,
        "max_zones": None,
        "caps": dict.fromkeys(CAPS, False),
        "upgradeable_to": [],
        "confidence": "partial",
        "notes": [
            "Brochure states one heat pump and gives no heating-system count; "
            "max_zones is null rather than guessed.",
            "The brochure never mentions Modbus TCP (only myUplink/internet). "
            "Whether the BMS register map applies to EcoLogic S is UNVERIFIED.",
        ],
    },
    {
        "id": "ecologic_s_ep",
        "model": "CTC EcoLogic S EP",
        "series": "EcoLogic S",
        "ctc_no": "589560302",
        "description": "Single heat pump control for CTC EcoPart (ground source).",
        "max_heat_pumps": 1,
        "max_zones": None,
        "caps": dict.fromkeys(CAPS, False),
        "upgradeable_to": [],
        "confidence": "partial",
        "notes": [
            "Same caveats as ecologic_s_ea: zone count unstated, Modbus TCP "
            "support unverified.",
        ],
    },
    {
        "id": "ecologic_ii_m",
        "model": "CTC EcoLogic II M",
        "series": "EcoLogic II",
        "ctc_no": "591300001",
        "description": "Smaller systems: up to 2 heat pumps, 2 heating systems.",
        "max_heat_pumps": 2,
        "max_zones": 2,
        "caps": dict(zip(CAPS, map(bool, _M))),
        "upgradeable_to": ["ecologic_ii_l"],
        "confidence": "brochure",
        "notes": [],
    },
    {
        "id": "ecologic_ii_l",
        "model": "CTC EcoLogic II L",
        "series": "EcoLogic II",
        "ctc_no": "591301001",
        "description": "Large installations: up to 10 heat pumps, 4 heating "
                       "systems, pool, solar, cooling.",
        "max_heat_pumps": 10,
        "max_zones": 4,
        "caps": dict(zip(CAPS, map(bool, _L))),
        "upgradeable_to": ["ecologic_ii_xl"],
        "confidence": "brochure",
        "notes": [],
    },
    {
        "id": "ecologic_ii_xl",
        "model": "CTC EcoLogic II XL",
        "series": "EcoLogic II",
        "ctc_no": "591302001",
        "description": "Maximum capacity: everything L has, plus active ground "
                       "source cooling and dual tank sensors.",
        "max_heat_pumps": 10,
        "max_zones": 4,
        "caps": dict(zip(CAPS, map(bool, _XL))),
        "upgradeable_to": [],
        "confidence": "brochure",
        "notes": [],
    },
]

CAP_LABELS = {
    "heat_pumps_1_2": "Heat pumps 1-2",
    "heat_pumps_3_10": "Heat pumps 3-10",
    "heating_systems_1_2": "Heating systems 1-2",
    "heating_systems_3_4": "Heating systems 3-4",
    "suppl_heat_0_10v": "Supplementary heating 0-10V",
    "suppl_heat_230v": "Supplementary heating 230V",
    "solar": "Solar energy",
    "pool": "Pool",
    "dhw_circulation": "DHW circulation",
    "ventilation": "Ventilation",
    "cooling_passive_ground": "Passive cooling (ground source)",
    "cooling_active_air": "Active cooling (air-to-water)",
    "cooling_active_ground": "Active cooling (ground source)",
    "dual_tank_sensors": "Dual tank sensors",
}


# --- option groups: which registers belong to which optional subsystem --------
# Matched on the register NAME prefix first (the manual's names are consistent:
# pool*, sun*, cool*, exb*/elh*/wood*, dth*), falling back to the description -
# because the manual leaves the Name column blank on some rows (they parse as
# "x"), and those rows are exactly the ones a name-only rule would drop into
# "core" and poll on a system that hasn't got the hardware.
#
# Order matters: sVentNightcoolValue is ventilation, not cooling.
GROUPS = [
    ("dhw",             ["dhw_circulation"], r"^s?dhw", r"\bhot water\b|\bdhw\b"),
    ("ventilation",     ["ventilation"],     r"^(svent|sfan)", r"ventilation|exhaust (air|fan)|night cool"),
    ("pool",            ["pool"],            r"^s?pool", r"\bpool\b"),
    ("solar",           ["solar"],           r"^sun", r"\bsolar\b"),
    ("cooling",         ["cooling_passive_ground", "cooling_active_air",
                         "cooling_active_ground"], r"^cool", r"\bcooling\b"),
    ("additional_heat", ["suppl_heat_0_10v", "suppl_heat_230v"],
                        r"^(exb|elh|wood)", r"immersion heater|external boiler|wood boiler|additional heat"),
    ("diff_thermostat", [], r"^dth", r"diff.{0,3}thermostat"),
]


def group_for(name: str, desc: str) -> tuple[str, list[str]]:
    """-> (group id, capability keys that must be enabled to use it).

    'core' means always present: outdoor temp, status, degree minutes, the stuff
    every install has regardless of options.
    """
    n, d = name.lower(), desc.lower()
    for gid, caps, name_re, desc_re in GROUPS:
        if re.search(name_re, n) or re.search(desc_re, d):
            return gid, caps
    return "core", []


def reg_json(r) -> dict:
    gid, caps = group_for(r.name, r.desc)
    return {
        "address": r.number,
        "name": r.name,
        "description": r.desc,
        "access": r.access,
        "dtype": r.dtype,
        "scale": r.scale,
        "unit": r.unit,
        "words": 2 if r.dtype.endswith("32") else 1,
        "group": gid,
        "requires_any_capability": caps,
    }


def field_json(f, kind: str, count: int) -> dict:
    return {
        "field": f.field,
        "key": f"{kind}{{n}}{f.field}",
        "base_address": f.base,
        "stride": f.stride,
        "description": f.desc,
        "access": f.access,
        "dtype": f.dtype,
        "scale": f.scale,
        "unit": f.unit,
        "words": 2 if f.dtype.endswith("32") else 1,
        # spelled out so a consumer never has to re-derive base+(n-1)*stride
        "addresses": {str(n): f.base + (n - 1) * f.stride
                      for n in range(1, count + 1)},
    }


def main() -> None:
    system = [reg_json(r) for r in R.SYSTEM_REGISTERS]

    dataset = {
        "schema_version": 1,
        "generated": str(date.today()),
        "generator": "scripts/build_dataset.py",
        "sources": [
            {"id": "bms_manual", "title": "CTC BMS Manual",
             "file": "BMS-Manual-16260016.pdf",
             "role": "authoritative for registers and protocol"},
            {"id": "ecologic_ii_brochure", "title": "CTC EcoLogic II M/L/XL",
             "file": "CTC-EcoLogic-M_L_XL.pdf", "doc_no": "17008029-1",
             "role": "authoritative for M/L/XL capabilities (p3 matrix)"},
            {"id": "ecologic_s_brochure", "title": "CTC EcoLogic S EA/EP",
             "file": "CTC-EcoLogic-S.pdf", "doc_no": "17003569_1",
             "role": "EcoLogic S; a datasheet, thin on capabilities"},
        ],
        "protocol": {
            "transport": "Modbus TCP",
            "default_port": 502,
            "read_function": "FC03 (holding registers)",
            "write_function": "FC16",
            "address_offset": 0,
            "max_registers_per_transfer": 100,
            "register_base": "registers live above 49999",
            "word_order_32bit": "LSB first, MSB second (value = MSB << 16 | LSB)",
            "sentinels": {
                "-9999": "sensor not fitted",
                "-10000": "sensor not fitted",
            },
            "notes": [
                "Reading an address the controller does not implement returns "
                "SILENCE, not an IllegalDataAddress exception - a wrong address "
                "is indistinguishable from a dead link and costs a full timeout.",
                "A block read succeeds only if EVERY address in it exists.",
                "The controller accepts one client and one outstanding request "
                "at a time; pipelining makes it stop answering entirely.",
            ],
        },
        "semantics": {
            "capability_vs_register_presence": (
                "Capability limits are a FILTER, not a statement about which "
                "addresses respond. Every HP1-HP10 and Zone1-Zone4 register "
                "answers on any controller; unfitted ones read 0. Use the "
                "controller's max_heat_pumps/max_zones to decide what to POLL, "
                "and nonzero data to decide what is actually FITTED."
            ),
            "presence_detection": (
                "'Is this heat pump fitted?' is answered by nonzero data, not by "
                "whether the register responds. Note -9999/-10000 is numerically "
                "nonzero but means the OPPOSITE - no sensor - so exclude the "
                "sentinels before counting."
            ),
            "units": (
                "INFERRED from each description; the manual gives only a scale "
                "factor. Treat as a hint."
            ),
        },
        "capabilities": [
            {"key": k, "label": CAP_LABELS[k]} for k in CAPS
        ],
        "controllers": CONTROLLERS,
        "registers": {
            "system": system,
            "families": {
                "heat_pump": {
                    "label": "Heat pump",
                    "instance_label": "HP{n}",
                    "max_instances": R.MAX_HEAT_PUMPS,
                    "limited_by": "max_heat_pumps",
                    "addressing": "base_address + (n-1) * stride",
                    "fields": [field_json(f, "hp", R.MAX_HEAT_PUMPS)
                               for f in R.HP_FIELDS],
                },
                "zone": {
                    "label": "Heating system (zone)",
                    "instance_label": "Zone{n}",
                    "max_instances": R.MAX_ZONES,
                    "limited_by": "max_zones",
                    "addressing": "base_address + (n-1) * stride",
                    "fields": [field_json(f, "hs", R.MAX_ZONES)
                               for f in R.ZONE_FIELDS],
                },
            },
        },
    }

    OUT.write_text(json.dumps(dataset, indent=2, ensure_ascii=False) + "\n")

    # --- sanity checks: fail loudly rather than ship a quietly wrong dataset ---
    n_sys = len(system)
    n_hp = len(R.HP_FIELDS) * R.MAX_HEAT_PUMPS
    n_zone = len(R.ZONE_FIELDS) * R.MAX_ZONES
    total = n_sys + n_hp + n_zone
    assert total == len(RAW), f"register count {total} != {len(RAW)} parsed"

    leaked = [r for r in system
              if re.search(r"(heat pump|compressor|hp|heating system)\s*\d+",
                           r["description"], re.I)]
    assert not leaked, f"per-instance registers leaked into system: {leaked[:3]}"

    addrs = [r["address"] for r in system]
    for fam in dataset["registers"]["families"].values():
        for f in fam["fields"]:
            addrs += [a for a in f["addresses"].values()]
            if f["words"] == 2:
                addrs += [a + 1 for a in f["addresses"].values()]
    dupes = {a for a in addrs if addrs.count(a) > 1}
    assert not dupes, f"address collision: {sorted(dupes)[:5]}"

    print(f"wrote {OUT.name}")
    print(f"  {len(CONTROLLERS)} controllers, {len(CAPS)} capabilities")
    print(f"  {n_sys} system + {n_hp} heat-pump + {n_zone} zone = {total} registers")
    counts: dict[str, int] = {}
    for r in system:
        counts[r["group"]] = counts.get(r["group"], 0) + 1
    print("  system register groups: "
          + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
