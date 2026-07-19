# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A HACS custom integration (`custom_components/ctc_bms/`, domain **ctc_bms**) for
CTC heat pumps over the BMS Modbus TCP protocol, plus its dev tooling in
`dev/`. Grown out of a hardware-verification CLI (kept as
`dev/ctc_modbus_test.py`) proven against a **CTC EcoLogic M** at
`192.168.1.100:502`, MB address (device id) 1.

| Path | Role |
|---|---|
| `custom_components/ctc_bms/registers.py` | **generated** register map — never hand-edit |
| `custom_components/ctc_bms/decode.py` | pure decode/encode + sentinel logic |
| `custom_components/ctc_bms/hub.py` | all Modbus I/O: lock, block reads, bisection, outage probe, dead cache |
| `custom_components/ctc_bms/coordinator.py` | poll set, device split, options handling |
| `dev/scripts/parse_bms.py` | BMS manual PDF → `dev/bms_registers.json` |
| `dev/scripts/gen_registers.py` | `dev/bms_registers.json` → `registers.py` |
| `dev/fake_ctc_server.py` | simulator on 127.0.0.1:5020 (`--port` to change) |
| `dev/ctc_modbus_test.py` | field CLI: `verify`, `devices`, `read`, `poll`, `scan` |

## Source of truth

The CTC **BMS manual PDF is authoritative** (copyrighted, NOT committed; the
parsed `dev/bms_registers.json` is). Protocol: registers above 49999, read
FC03, write FC16 (even single registers), offset 0, **max 100 registers per
transfer**. To change the register map: fix the parser/generator and re-run

```sh
python dev/scripts/parse_bms.py /path/to/BMS-Manual-16260016.pdf
python dev/scripts/gen_registers.py
```

Units are inferred from descriptions (the manual only gives scale factors) —
treat them as hints.

## The map is arrays

Three shapes = the HA devices: `SYSTEM_REGISTERS` (224, flat), `HP_FIELDS`
(25 fields × 10 pumps), `ZONE_FIELDS` (20 fields × 4 zones); member n lives at
`base + (n-1)*stride` (stride 2 for the 32-bit HP fields). The manual leaves
the Name column blank on some array rows, so the generator matches
descriptions too and only promotes complete families —
`tests/test_registers.py::test_no_array_rows_leaked_into_system` is the
regression gate (that bug shipped twice).

**Every HP/Zone register answers whether or not the hardware exists** (absent
= 0). Presence = **≥2 registers with real nonzero data**, where the
−9999/−10000 sentinel counts as *absence* (it is numerically nonzero — never
count raw nonzeros). A 0 is a real reading (idle compressor).

## Hard-won Modbus gotchas (do not re-learn these)

- **Nonexistent register = silence**, not an exception — indistinguishable
  from a dead link, costs a full timeout. Hence: probe register **62000**
  (always exists) to tell "link down" from "absent address"; suspect the
  address before the network.
- **One absent address kills the whole block read** → bisect, cache dead
  addresses (`hub.dead_addresses`).
- **The controller cannot pipeline** — one outstanding request, ever
  (`hub._lock` covers reads *and* writes).
- **32-bit values are LSB first, MSB second** (`MSB << 16 | LSB`) — deliberate
  anti-convention; don't "fix" to big-endian.
- pymodbus ≥3.13: kwarg is **`device_id=`** (not `slave=`/`unit=`);
  `retries=1` (default 3 triples every dead-address timeout). Server side:
  `ModbusDeviceContext`, and the fake server bases its data block at `BASE+1`
  to compensate pymodbus's legacy address+1 lookup — changing that shifts every
  simulated register by one.
- Community integration (another community CTC integration) mislabels 62100 as
  "heat pump status" (it's HP4's brine-out); real status is 62017. It also
  invents float32 registers — the manual has none.

## Dev workflow

```sh
python3.14 -m venv .venv && source .venv/bin/activate   # HA needs >=3.14
pip install -r requirements_dev.txt
pytest -q                     # fast, no sockets

python dev/fake_ctc_server.py &          # simulator
pip install homeassistant
ln -s "$PWD/custom_components/ctc_bms" config/custom_components/ctc_bms
hass -c config                # UI: add integration -> 127.0.0.1:5020
```

Code edits need a HA restart (Ctrl+C, rerun `hass`) — Python isn't
hot-reloaded; the UI "Reload" only re-runs setup with stale code. Config
entries persist in `config/.storage`.

Release = bump `manifest.json` version → tag `vX.Y.Z` → GitHub release; HACS
picks it up (installed as a custom repository). CI runs hassfest, HACS
validation and pytest.

## Writes are real

Number entities write to a live heating system. Curated setpoints only
(`SETPOINT_*` in `const.py`) with conservative limits; enum/mode RW registers
are deliberately excluded from v1 (they belong in select/switch entities).
Never widen limits or add setpoints without checking the manual's max/min for
that parameter.
