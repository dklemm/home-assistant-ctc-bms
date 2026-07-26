# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A HACS custom integration (`custom_components/ctc_bms/`, domain **ctc_bms**) for
CTC heat pumps over the BMS Modbus TCP protocol, plus its dev tooling in
`dev/`. Grown out of a hardware-verification CLI (kept as
`dev/ctc_modbus_test.py`) proven against a **CTC EcoLogic M** on port 502, MB
address (device id) 1. The CLI takes `--host` or `$CTC_HOST` — the controller's
address is site-specific and deliberately not recorded here.

| Path | Role |
|---|---|
| `custom_components/ctc_bms/registers.py` | **generated** register map — never hand-edit |
| `custom_components/ctc_bms/decode.py` | pure decode/encode + sentinel logic |
| `custom_components/ctc_bms/hub.py` | all Modbus I/O: lock, block reads, bisection, outage probe, dead cache |
| `custom_components/ctc_bms/coordinator.py` | poll set, device split, options handling |
| `custom_components/ctc_bms/groups.py` | hand-written: which HA device each system register lands on |
| `custom_components/ctc_bms/models.py` | hand-written: controller model → default subsystems |
| `custom_components/ctc_bms/overrides.py` | hand-written: per-register unit fixes + disabled-by-default |
| `custom_components/ctc_bms/names.py` | hand-written: the entity name for every register that ships |
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
treat them as hints. Where an inferred unit is wrong (the supply currents come
out unitless when they're Amps) or a register is real but rarely wanted (the
3-phase currents, immersion-heater power — created but shipped disabled), fix it
in `overrides.py` keyed by register number, **not** in the generated map. An
`Override` carries a unit, an optional value `factor` (e.g. ×1000 to show a kW
register in W), and `enabled_default`.

**`reg.name` is not a display name.** It is the manual's Name column: shorthand
(`CurrRPS`, `sSetPDHW`, `sDM`), and *blank* on many rows, where the generator
falls back to `slug(desc)[:40]` — which drags the value legend into the name
(`evk_shunt_state_0_close_1_inactive_2_ope`) and flattens real camelCase
(`hotwatervalve`). So every register that ships as an entity is named by hand in
**`names.py`** — `NAME_SYSTEM` / `NAME_HP_FIELDS` / `NAME_ZONE_FIELDS`, keyed the
same way as the curated tables in `const.py` (number for the flat map, field name
for the arrays). Names are HA sentence case, acronyms excepted, and omit what
`has_entity_name` already supplies from the device — hot water's `sDHWTemp` is
just "Temperature", because HA shows it as "CTC Hot Water Temperature".
`register_entity_name()` still derives a name for anything unlisted, but
`tests/test_names.py::test_every_shipped_register_has_a_curated_name` is the gate
that no shipped entity relies on that. Renaming is display-only — the unique_id
is the register number — so existing installs keep their old `entity_id` while
new ones get a slug from the new name.

That table is *not* in `overrides.py`, and the distinction is worth keeping: an
override replaces something the generator got wrong, whereas the manual supplies
no display names at all, so `names.py` is authored data like `groups.py` and
`models.py`. Nor can names live upstream — the generator would need the
uncommitted PDF, and `dev/bms_registers.json` is rewritten wholesale by the next
`parse_bms.py` run (and records what the manual says, not what we call things).
Naming must stay editable from a clone.

## The map is arrays

Three shapes = the HA devices: `SYSTEM_REGISTERS` (224, flat), `HP_FIELDS`
(25 fields × 10 pumps), `ZONE_FIELDS` (20 fields × 4 zones); member n lives at
`base + (n-1)*stride` (stride 2 for the 32-bit HP fields). The manual leaves
the Name column blank on some array rows, so the generator matches
descriptions too and only promotes complete families —
`tests/test_registers.py::test_no_array_rows_leaked_into_system` is the
regression gate (that bug shipped twice).

`SYSTEM_REGISTERS` is further split for presentation by `groups.py` (ordered
prefix rules, first match wins) into the controller plus six subsystem devices —
DHW, Solar, Pool, Cooling, Ventilation, AddHeat — with the `hcN` heating curves
routed to Zone N. That file is hand-written on purpose: regenerating
`registers.py` must not clobber it, and
`tests/test_groups.py::test_grouping_is_total_and_disjoint` is the gate that a
regenerated map can't silently drop registers.

**Subsystems cannot be detected — pick them from the model.** Verified on the
EcoLogic M: `verify --system` reports *87 present, 0 not implemented*. Every
system register answers, and unfitted hardware returns plausible values, not
sentinels — with no solar fitted, `sunTempOut`/`sunTempIn` read 1000 and
`sunPump` 100%; with no ventilation unit, `sFanExhaustPct` reads 100% and
`sVentMaintFilterDays` 83. So neither silence nor `is_present` works here, and
`models.py` maps the controller model to default subsystems instead
(`sProductType` 62253 = **14** on an EcoLogic M; `sSystemType` 62207 is the
hydraulic layout configured in the menus, *not* the model).

The model only seeds the checkboxes — `CONF_SUBSYSTEMS` in the options flow is
the real control, because a model permits more than any one install has. Never
make the model table a hard filter. An entry with no stored list keeps every
subsystem, so upgrades never remove entities.

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
- Don't cross-check register meanings against other community CTC integrations.
  At least one labels 62100 "heat pump status" (it's HP4's brine-out; real
  status is 62017) and defines float32 registers, of which the manual has none.
  The manual and the hardware are the only sources worth trusting.

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

**`CtcCoordinator.platform_for()` is the only gate on entity creation**, and the
reason a register can never appear on two platforms — every platform file filters
on it. Read-only registers always become an entity (`valve` / `binary_sensor` if
listed, else `sensor`); a *writable* register becomes one **only if a curated
table in `const.py` names it**, because the write goes to a live heating system:

| table | platform | keyed by |
|---|---|---|
| `SETPOINT_SYSTEM` / `_HP_FIELDS` / `_ZONE_FIELDS` | number | number / field |
| `SELECT_SYSTEM` / `_HP_FIELDS` / `_ZONE_FIELDS` | select | number / field |
| `SWITCH_SYSTEM` / `_HP_FIELDS` / `_ZONE_FIELDS` | switch | number / field |
| `READ_ONLY_RW` | sensor | number |

Which table applies is a property of the register's **shape**, not of the device
it's displayed on: `_lookup()` keys the flat map by number and the arrays by
field name, so the `hcN` programs stay number-keyed even though `groups.py` shows
them on a Zone device. Unlisted writable registers (~140 of them) produce no
entity *and are not polled*. `CONF_SETPOINTS` gates all three writable platforms
at once; `READ_ONLY_RW` survives it, since nothing there can write.

Never widen limits or add setpoints without checking the manual's max/min, and
only add a select/switch whose *complete* value set the manual spells out — the
controller accepts undocumented values silently. `READ_ONLY_RW` is the holding
pen for registers that are writable and obviously boolean but have no documented
legend (`pool_enable` 61658, `sVentNightcoolValue` 61656): readable now,
promotable once the polarity is confirmed on hardware. `sVentilationMode` 61655
and `sVentAwayMode` 61657 have no legend at all.

Switch polarity follows the **register**, not HA: HP `Blocked` reads 0 when the
pump is blocked, so `SWITCH_HP_FIELDS` maps on→0 and turning the switch on
blocks the pump. An entity whose name and value disagree is worse.

## Valves and booleans are a map judgement, not an HA one

Every valve and shunt register in the map is **read-only** — there is no
writable valve register — so `valve.py` reports state and advertises no
OPEN/CLOSE feature. Only the two-position diverters (`HotWaterValve` 62313,
`HotWaterValve2` 62326) are valves. The six modulating shunts (Zone
`ShuntState` 62308-62311, EVK 62319, ExtBoiler 62320) are **not**: `0=Close,
1=Inactive, 2=Open` is which way the actuator is being *driven*, not where the
valve sits, and HA's valve domain has no state for "holding at an unknown
position" — which is what Inactive means and what a mixing valve does most of
the time. They stay sensors.

`Active Cooling: Valve` 62321 looks like a third diverter and is not one: it
reads **25600** (0x6400) on the EcoLogic M, so whatever it holds, it isn't
two-state. Field values beat names here.

A read-only register with a documented legend belongs in `ENUM_*`, not
`SELECT_*` — it becomes a `SensorDeviceClass.ENUM` sensor reporting "Compressor
on, heating" instead of "3". A select would let you write a status the pump
computes for itself. Gaps are safe: HP `Status` documents 0-8 then jumps to
30-33, and an undocumented value reads unknown rather than an invented option
(which HA would reject as not in `options`). `ehsStatus` 62177 and `poolStatus`
62178 are named "Mode" but have no legend at all, so they stay numeric.

**`parse_bms.py` truncates descriptions at 160 characters** (`desc[:160]`), which
silently loses the tail of the two longest legends — 62017 stops at `5=` and
62365 mid-word. The `ENUM_*` labels were transcribed from the PDF, not from
`dev/bms_registers.json`, so they are complete. Raising that limit would not
disturb `registers.py`: names for blank-Name rows come from `slug(desc)[:40]`
and the stored descriptions from `desc[:70]`, so only the JSON gets richer.

Same rule for `BINARY_SYSTEM`: a register holding 0-100 would read "on" at 1%,
so only registers boolean *by evidence* qualify — a documented 0/1 legend
(`sunStatus`), or an on/off output name where the manual annotates its
percentage siblings and leaves this one bare (`DHWPump: 0-100` vs
`RadiatorPump1`). Deliberately still sensors: the EL1-EL3 relays (a 2-bit
field), 62323 (a percentage), the solar tank/bedrock *selections*, `E1`/`E4`
(no documented semantics), and 62365 (documents 0 and 1, reads 2 in the field).

Reclassifying a register between platforms is a **breaking change**: the
unique_id is the register number, so the old entity would linger for ever as an
unavailable "restored" entry. `_drop_reclassified_entities()` in `__init__.py`
removes it at setup; its recorded history stays in the database under the old
`entity_id`, and dashboards or automations referencing that id need fixing by
hand.
