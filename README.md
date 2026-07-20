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
  sensors read −9999/−10000). The flow detects what is actually fitted and only
  creates those devices. Idle hardware (summer!) can be force-enabled in
  Options.
- **Proper devices**: one HA device per real thing — System, Heat Pump *n*,
  Heating System *n* — with entities attached to the right one.
- **Fast, gentle polling**: registers are read in ≤100-register blocks (one
  block costs the same ~10 ms as one register), one request outstanding at a
  time (the controller cannot pipeline), default every 30 s.
- **Writable setpoints**: a curated set of RW registers (room temperature
  setpoints, night reduction, DHW stop temperature, compressor max RPS) as
  number entities, written with FC16. Can be disabled entirely in Options for
  a read-only integration.
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

Manual install: copy `custom_components/ctc_bms/` into your HA `config/custom_components/`.

## Options

Settings → Devices & Services → CTC Heat Pump (BMS) → **Configure**:

- Poll interval (5–300 s, default 30)
- Which heat pumps / heating systems get devices (pre-filled by detection)
- Whether writable setpoint entities are created

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
  is committed so the map can be regenerated without it.

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
| real pump | `192.168.1.100` | 502 | 1 |

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

## License

GPL-3.0 — see [LICENSE](LICENSE).
