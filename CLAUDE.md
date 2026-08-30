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
| `custom_components/ctc_bms/hub.py` | all Modbus I/O: block reads, bisection, outage probe, dead cache |
| `custom_components/ctc_bms/coordinator.py` | poll set, device split, options handling |
| `custom_components/ctc_bms/controls.py` | hand-written: the 1000-range control registers |
| `custom_components/ctc_bms/hold.py` | keeps a control asserted; 60 s refresh, 5 min expiry |
| `custom_components/ctc_bms/groups.py` | hand-written: which HA device each system register lands on |
| `custom_components/ctc_bms/models.py` | hand-written: controller model → default subsystems |
| `custom_components/ctc_bms/overrides.py` | hand-written: per-register unit fixes + disabled-by-default |
| `custom_components/ctc_bms/names.py` | hand-written: the entity name for every register that ships |
| `dev/scripts/parse_bms.py` | BMS manual PDF → `dev/bms_registers.json` |
| `dev/scripts/gen_registers.py` | `dev/bms_registers.json` → `registers.py` |
| `dev/fake_ctc_server.py` | simulator on 127.0.0.1:5020 (`--port` to change) |
| `dev/ctc_modbus_test.py` | field CLI: `verify`, `devices`, `read`, `poll`, `scan`, `control`, `probe`, `discover-di` |

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
register in W), `enabled_default`, and `zero_is_unknown`.

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

**Except where it can't be.** The controller has a *second*, undocumented
"no reading" marker: a plain 0, used where −9999/−10000 would have been honest.
The map documents five DHW tank temperatures and an install populates only the
ones its arrangement has, parking the rest at 0 — on the EcoLogic M just 62276
carries the tank (610 = 61.0 °C) while 62002, 62003 and 62275 sit at zero, so
the entity named plainly *Temperature* (62003) led the Hot Water device with a
headline 0.0 °C beside 61 °C water. Those four carry `zero_is_unknown` in
`overrides.py` and `is_sentinel()` reads it, so they report *unknown* instead.

**Never generalise that flag.** It is only ever right for a quantity that
physically cannot sit at zero — stored water is never 0.0 °C, and a DHW
setpoint of 0 is not a setting the controller can hold. 62000 outdoor
temperature (0 °C is an ordinary morning) and 62279 DHW capacity (0 % is an
empty tank) are deliberately left alone, and
`tests/test_overrides.py::test_zero_is_unknown_is_confined_to_temperatures` is
the gate. `is_present()` is unaffected either way — it already counts nonzeros.

Renaming was the tempting fix and is the wrong one: 62003 *is* the hot water
temperature on the controllers that populate it, and the register number is the
unique_id, so shuffling names only moves the lie to a different install.

## Hard-won Modbus gotchas (do not re-learn these)

- **Nonexistent register = silence**, not an exception — indistinguishable
  from a dead link, costs a full timeout. Hence: probe register **62000**
  (always exists) to tell "link down" from "absent address"; suspect the
  address before the network.
- **One absent address kills the whole block read** → bisect, cache dead
  addresses (`hub.dead_addresses`).
- **The controller serves one Modbus TCP client at a time**, and a second one
  fails in a way that looks like a broken client, not a busy pump: the TCP
  handshake is *accepted*, then the controller sends RST on the first PDU. A
  plain `socket.create_connection()` therefore succeeds while pymodbus reports
  "could not connect" in ~0.1 s, which reads exactly like a bad address or a
  regression in whatever changed last. Before debugging the client, stop
  everything else that polls the pump — the production HA is the usual culprit.
  `cannot_connect` in the config flow already says so; believe it.
- **The controller cannot pipeline** — one outstanding request, ever. The
  `ModbusConnection` serializes every request over the link, reads and writes
  alike, so `hub.py` needs no lock of its own. Don't add code that talks to the
  controller outside it.
- **32-bit values are LSB first, MSB second** (`MSB << 16 | LSB`) — deliberate
  anti-convention; don't "fix" to big-endian.
- The integration talks **`modbus-connection[pymodbus]`**, not pymodbus:
  `ModbusConnection(ModbusTcpParams(...))` → `for_unit(device_id)` →
  `read_holding_registers(address, count) -> list[int]`. It owns the device id
  and sets `retries=0`, so a dead address costs exactly one timeout, and the
  first request opens the link (there is no connect step — `async_probe()` is
  it). **Only `ModbusTimeoutError` (the CTC's silence) and
  `IllegalDataAddressError` (exception code 2, what the fake server and a
  politer firmware answer with) mean "absent address"**; every other
  `ModbusError` is a link or device failure and raises. Don't re-collapse the
  two groups — bisecting a dropped link caches live registers as dead for
  ever. `ModbusTimeoutError` also subclasses the builtin
  `TimeoutError`, and `coordinator.py` catches that for its own poll-overran
  guard, so none may escape `hub.py`.
- The **fake server is still pymodbus** (the library ships no server):
  `ModbusDeviceContext`, and it bases its data block at `BASE+1` to compensate
  pymodbus's legacy address+1 lookup — changing that shifts every simulated
  register by one. `dev/ctc_modbus_test.py` is also still pymodbus, and
  synchronous.
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

## Writes are real — and they wear the controller out

Every writable register we ship is a **stored parameter**, and the manual warns
that those have a limited write-cycle count ("you risk breaking the controller
of the heat pump installation"). So the writable entities are for settings a
human changes, not for closed-loop control, and the manual's answer for
anything that must change often is the **1000-range control registers** — see
the next section, which is where an automation belongs.

The writable platforms are **off by default**: the config flow's `setpoints`
step asks and defaults to False, and *stores* the answer. The fallback in
`coordinator.py` and the options flow stays `True` on purpose — an entry
predating that step has no stored value and must keep the entities it already
created, the same rule the subsystem list follows. `CONF_CONTROLS` falls back to
`False` in both places for the opposite reason: no entry predates *its* step, so
there is nothing to preserve. Three defaults, three different questions — don't
"tidy" any of them into agreement.

Nothing writes except a service call or a control-register refresh: polling is
read-only, and there is no write at setup, on reconnect, or on any other
schedule. **`CtcEntity.async_write_raw()`
is the single write path** for all three writable platforms, and it drops a
write whose raw word already matches the last poll — HA does not suppress a
service call that matches current state (`switch.turn_on` on an already-on
switch still reaches the entity), so without it a re-asserting automation would
burn a cycle per run for ever. It compares **raw words, not engineering
values**, which is what makes a number request that rounds to the stored word a
no-op. The comparison can be one scan interval stale (change a setting at the
controller's panel and a write of the value HA last saw is skipped until the
next poll) — accepted, since the alternative is a read before every write.

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

## The 1000-range controls are the opposite trade-off

`controls.py` is the hand-authored table of the 36 control registers (manual
page 22) and `hold.py` is what keeps one asserted. They cost **no write cycles**
— this is where an automation belongs — but they are **write-only**, reset on
controller restart, and **discarded 5 minutes after the last write**. That last
property is the safety feature: `ControlHold` re-writes every 60 s, and a
control nobody refreshes is undone by the controller itself. Nothing is
re-asserted at startup, on reload, or on reconnect, and `async_shutdown()`
writes nothing — coming up released is the fail-safe, not an oversight.

The table is hand-written for the same reason `groups.py` and `names.py` are:
`parse_bms.py` bounds its rows to 60000-62999 and reads pages 23-45, while this
table is on page 22 with a different column set — and no **Factor** column at
all. So every numeric control **borrows its scale from the stored parameter it
shadows** (1002 ← 61572 `RPSMax`, 1033 ← 62001 `sStopTempDHW`), `scale_from`
records which, and `tests/test_controls.py::test_inferred_scales_match_their_sibling`
fails loudly if a regenerated map ever moves one. `probe 1002 400 800 --yes`
settles any of them on hardware.

**Never poll a control register.** They are write-only, so a read is silence,
which `hub.async_read_addresses` cannot tell from a dead link — it would bisect
through timeouts and poison `dead_addresses` for ever. They stay out of
`_wanted` by construction, and a test pins it.

**Releasing is the absence of a write, not writing 0.** The manual documents
`0 = Economy` on 1007 and `0 = Off` on 1015-1019, so a 0 is a *command* — on a
zone mode, the command that turns heating off. Selects therefore get an explicit
`"Not controlled"` option; numbers read *unknown* when released and are released
by setting them to 0, which is safe to overload because 0 is never a setting
these registers can hold (a DHW tank at 0 °C) or, for the curve offsets, is
exactly what released means. **1100 is the one exception** and *is* written to
0 to release: 0 there is the documented "all 8 bits open".

Register 1100 carries nine entities over **one held word** — eight virtual
digital input switches (disabled by default) and, once the options flow says
which bits are SmartGrid A and B, a SmartGrid select writing the manual's truth
table. They read-modify-write the same word, so they cannot fight; two
*masters* still can, so disable the config entry before running `probe` or
`discover-di`. Which DI carries which function is set in the controller's own
menus and cannot be looked up — only configured, or found with `discover-di`.

Control entity names all end in **"override"**, which is what keeps them apart
from the stored parameter beside them: the Hot Water device carries both `Mode`
(61500, a setting) and `Mode override` (1007, a command). Those names live on
the `Control` row, not in `names.py` — the row is hand-written already.

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
