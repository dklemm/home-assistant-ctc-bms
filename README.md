<img src="custom_components/ctc_bms/brand/icon.png" alt="" width="96" align="right">

# CTC Heat Pump (BMS) for Home Assistant

A Home Assistant custom integration for **CTC heat pumps and controllers** that
speak the CTC **BMS Modbus TCP** protocol (EcoLogic M, and other current CTC
controllers exposing the 6xxxx register map). Developed and verified against a
CTC EcoLogic M.

It talks pymodbus directly — no YAML `modbus:` platform, no templates:

- **UI config flow**: host, port, Modbus device ID; connection is verified
  against a register that is guaranteed to exist.
- **Hardware detection**: the BMS answers for all 10 possible heat pumps and 4
  heating systems whether they exist or not (absent hardware reads 0, unfitted
  sensors read −9999/−10000). The flow detects which heat pumps and heating
  systems are actually fitted and only creates those devices. Idle hardware
  (summer!) can be force-enabled in Options.
- **Subsystems from the model**: hot water, solar, pool, cooling, ventilation
  and additional heat get their own devices, but which of them exist *cannot*
  be detected — unfitted hardware answers with plausible values rather than
  sentinels (no solar fitted still reports a panel temperature). So the
  controller's model seeds a set of checkboxes and you correct it in Options.
- **Proper devices**: one HA device per real thing — System, Heat Pump *n*,
  Heating System *n*, plus a device per subsystem — with entities attached to
  the right one.
- **Fast, gentle polling**: registers are read in ≤100-register blocks (one
  block costs the same ~10 ms as one register), one request outstanding at a
  time (the controller cannot pipeline), default every 30 s.
- **Writable setpoints, selects and switches — off by default**: a curated set
  of RW registers (room temperature setpoints, night reduction, DHW stop
  temperature, compressor max RPS, heating and hot water modes, blocking a heat
  pump) written with FC16. Deliberately small — the write goes to a live heating
  system, so a register only becomes writable if the manual documents its limits
  or its complete value set. Setup asks whether to create them and defaults to
  *no*; read
  [Do not automate the writable entities](#do-not-automate-the-writable-entities)
  before you tick it.
- **The right entity type**: documented states become enum sensors reporting
  "Compressor on, heating" rather than `3`; the two-position diverter valves
  become valves; pump on/off outputs become binary sensors.
- Correct decoding of negative temperatures, the −9999/−10000 "no sensor"
  sentinel (shown as *unknown*), and the controller's LSB-first 32-bit values.

The register map (554 registers) is generated from CTC's official BMS manual
(*User Manual-BMS Manual-16260016*), not copied from other integrations.

## Installation (HACS)

1. HACS → three-dot menu → **Custom repositories** →
   `https://github.com/dklemm/home-assistant-ctc-bms`, category **Integration**.
2. Install **CTC Heat Pump (BMS)**, restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → *CTC Heat Pump (BMS)*.
4. Enter the controller's IP, port 502, MB address (device ID) 1 — these are on
   the controller's *Settings → Communication* screen, where Modbus TCP must be
   enabled.
5. Confirm the model, then choose whether to create writable entities. This
   **defaults to off** and the integration is read-only until you turn it on —
   see the warning below.

Manual install: copy `custom_components/ctc_bms/` into your HA `config/custom_components/`.

## Options

Settings → Devices & Services → CTC Heat Pump (BMS) → **Configure**:

- Poll interval (5–300 s, default 30)
- Which heat pumps / heating systems get devices (pre-filled by detection)
- Controller model, and which subsystems get devices (pre-filled from the
  model; unticking one removes its device and stops polling its registers)
- Whether writable entities are created

## ⚠️ Do not automate the writable entities

They are **off by default** — setup asks, and the answer is no unless you change
it, so a fresh install is read-only. Existing installs keep whatever they
already had; upgrading never removes entities.

**The numbers, selects and switches write to the controller's stored
parameters, and those have a limited number of write cycles.** CTC's BMS manual
puts it plainly:

> These parameters must not be changed a lot of times. If you do so you risk
> breaking the controller of the heat pump installation. There is a limit to
> the amount of write cycles!

They are safe to change the way a person changes a setting — by hand, from the
dashboard, now and then. They are **not** safe as the output of a control loop.
The tempting automations are exactly the dangerous ones:

- blocking a heat pump on an electricity price signal,
- nudging a room setpoint every few minutes from solar production,
- a script that re-asserts "the right" mode on a timer.

At one write every 15 minutes that is ~35,000 write cycles a year; once a
minute is half a million. Typical EEPROM endurance is around 100,000.

Two things reduce the risk, neither of which makes automation safe:

- The integration **never writes on its own**. Polling is read-only; nothing is
  written at setup, on reconnect, or on a schedule. Every write is caused by a
  service call.
- A write that would not change the register is **dropped** — Home Assistant
  does not suppress `switch.turn_on` on an already-on switch, so an automation
  that re-asserts a steady value costs nothing. An automation whose value
  actually moves still writes every time it moves.

If you need frequent control, the manual's answer is the **1000-range control
registers** (`Max RPS`, zone modes, DHW mode, SmartGrid via virtual digital
inputs). They carry no write-cycle cost, but expire after 5 minutes unless
refreshed — and this integration does not implement them yet.

*"Create writable entities"* in Options turns them on or off at any time;
leaving it off removes the risk entirely.

## Development

Everything for developing without (or against) the real pump lives in
[dev/](dev/):

- `fake_ctc_server.py` — a local simulator seeded with a running HP1, a
  negative temperature, a no-sensor sentinel and a 32-bit counter:
  `python dev/fake_ctc_server.py` then point the config flow at
  `127.0.0.1:5020`.
- `ctc_modbus_test.py` — a standalone CLI for verifying registers against the
  real pump (`verify`, `devices`, `read`, `poll`, `scan`).
- `scripts/` — the PDF → JSON → `registers.py` generator pipeline. The
  register map is **generated**; never hand-edit
  `custom_components/ctc_bms/registers.py`. The BMS manual PDF is copyrighted
  and not part of this repo; `dev/bms_registers.json` (the parsed intermediate)
  is committed so the map can be regenerated without it. `parse_bms.py` takes
  the path to your own copy of the manual.

  The register numbers, scale factors and description strings in
  `dev/bms_registers.json` are extracted from CTC's BMS manual (*User
  Manual-BMS Manual-16260016*) and remain CTC's copyright; they are included
  here only as the protocol description needed to talk to the hardware. CTC is
  not affiliated with this project.

### Test in Docker (a real HA, like production)

```sh
cd dev && docker compose up --build      # HA on http://localhost:8123
```

Brings up Home Assistant with this integration mounted in, plus the simulated
controller. After the onboarding screen: Settings → Devices & Services → **Add
Integration** → *CTC Heat Pump (BMS)*, then either

| target | host | port | device id |
|---|---|---|---|
| simulator | `ctc-sim` | 5020 | 1 |
| real pump | your controller's address | 502 | 1 |

Bridge networking reaches both — the real controller on the LAN needs no host
networking, since the integration only makes an outbound TCP connection and
uses no discovery. The simulator is also published on the host at
`127.0.0.1:5020` for the CLI.

Python is not hot-reloaded; after editing the integration:

```sh
docker compose restart homeassistant
docker compose logs -f homeassistant     # component logs at debug
```

### Local dev loop (macOS/Linux, Python ≥ 3.14)


```sh
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements_dev.txt
pytest                        # unit + integration tests, no sockets

# run a real HA against the simulator:
pip install homeassistant
mkdir -p config/custom_components
ln -s "$PWD/custom_components/ctc_bms" config/custom_components/ctc_bms
python dev/fake_ctc_server.py &
hass -c config                # add the integration at 127.0.0.1:5020
```

## Notes for other CTC models

The BMS protocol is shared across current CTC controllers, but which registers
are populated differs per model. Registers a controller does not implement are
answered with *silence* (not an error) — the integration bisects around them
once and remembers. Issue reports with a diagnostics download (which includes a
raw register snapshot) are welcome.

## Brand icon

The icon lives in [custom_components/ctc_bms/brand/](custom_components/ctc_bms/brand/)
as `icon.png` (256×256) and `icon@2x.png` (512×512). Since **HA 2026.3.0** a
custom integration serves its own brand images from that folder, and they take
priority over `brands.home-assistant.io`. No `manifest.json` entry is needed, and
no `logo.png` — the mark is square, and HA falls back to the icon wherever a logo
would be used.

Do *not* send these to [home-assistant/brands](https://github.com/home-assistant/brands):
it stopped accepting custom integrations when 2026.3.0 landed, and a bot
auto-closes such PRs. That also means the HACS action's `brands` check can never
pass, which is why [validate.yml](.github/workflows/validate.yml) ignores it.

On HA **older than 2026.3.0** there is now no way to supply an icon at all, so
those installs show the default placeholder. Nothing else is affected.

`icon.svg` is the only file to edit; re-render both PNGs from it with `cairosvg`
(`svg2png(url=..., output_width=256/512)`). Not with macOS `qlmanage` — it pads
the artwork into a thumbnail canvas, leaving whitespace around the mark.

## License

GPL-3.0 — see [LICENSE](LICENSE). The CTC logo in
[custom_components/ctc_bms/brand/](custom_components/ctc_bms/brand/) is CTC's
trademark and is not covered by that licence; it is included solely to identify
the hardware this integration talks to.
