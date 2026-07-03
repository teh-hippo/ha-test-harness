# HomeKit smoke: headless HAP verification

`smoke.py` is a parameterised [aiohomekit](https://github.com/Jc2k/aiohomekit)
client that pairs with a running Home Assistant HomeKit bridge, dumps the full
accessory database, asserts a set of characteristics, then unpairs. It lets you
verify HomeKit behaviour end to end with no phone and no iOS Home app.

`aiohomekit` is the same library Home Assistant's `homekit_controller`
integration uses, so it pairs and reads the accessory database exactly the way
an iOS Home client would.

This copy ships the HeaterCooler example: it proves a routed `climate` entity is
exposed as a native HeaterCooler accessory (HAP service `BC`) while an un-routed
climate entity stays a Thermostat (HAP service `4A`). The discovery, pairing,
dump and teardown machinery is generic; only the two assertion blocks in `run()`
are domain specific, so adapt them (or the `--heatercooler-name` /
`--thermostat-name` matchers) for another integration.

## Setup

```bash
cd homekit
uv venv
uv pip install aiohomekit
```

`aiohomekit` pulls in `zeroconf`, which the script needs for mDNS discovery.

## What the script does

1. Discovers the bridge over zeroconf (mDNS) by HAP device id. If mDNS fails and
   an `--ip` is supplied, it falls back to a directly constructed IP:port
   connection. Both paths are handled automatically.
2. Pairs with the PIN using an unauthenticated pair-setup, then adds the pairing.
   The pairing is persisted to `--pairing-file` so an interrupted run can be
   cleaned up on the next invocation.
3. Lists every accessory, with each service (by type) and characteristic (by
   type and current value).
4. Asserts the routed accessory exposes the HeaterCooler service (`BC`) with
   Active, CurrentHeaterCoolerState, TargetHeaterCoolerState,
   CoolingThresholdTemperature, HeatingThresholdTemperature and RotationSpeed,
   and reads their live values.
5. Asserts the un-routed accessory exposes a Thermostat service (`4A`) and no
   HeaterCooler, proving the routing is opt-in and scoped correctly.
6. Prints a PASS/FAIL summary and exits non-zero on any failure.
7. Removes the pairing so the bridge returns to unpaired (`sf=1`) and confirms
   it is re-pairable by starting a fresh pair-setup (M1 to M2) and aborting
   before M3, so no new pairing is created.

## Run it

Every target detail is a flag (or environment variable), so the same script
targets any bridge by swapping the device id, PIN and, optionally, IP and port.
Read the device id and PIN from the bridge's `.storage/homekit.*` file or the
Home Assistant log after the bridge starts.

```bash
cd homekit
.venv/bin/python smoke.py \
    --device-id XX:XX:XX:XX:XX:XX --pin XXX-XX-XXX \
    --ip 127.0.0.1 --port 21063
```

Equivalently via environment variables:

```bash
HC_DEVICE_ID=XX:XX:XX:XX:XX:XX HC_PIN=XXX-XX-XXX HC_IP=127.0.0.1 HC_PORT=21063 \
    .venv/bin/python smoke.py
```

If the routed and un-routed entities have different names on your bridge,
override the matchers (they default to the substrings `daikin` and `dual`):

```bash
.venv/bin/python smoke.py --device-id XX:XX:XX:XX:XX:XX --pin XXX-XX-XXX \
    --heatercooler-name <substring> --thermostat-name <substring>
```

You can also pin accessories by aid with `--heatercooler-aid` and
`--thermostat-aid` when names are ambiguous.

## Useful flags

- `--force-fallback` skips mDNS and connects straight to `--ip:--port`. Useful
  when zeroconf is unavailable.
- `--keep-pairing` leaves the pairing in place (persisted to `--pairing-file`)
  instead of removing it, for reuse or a follow-up iOS pairing.
- `--discovery-timeout` sets the zeroconf discovery timeout (default 15s).

Run `.venv/bin/python smoke.py --help` for the full list.

## Adapting it to another integration

The script is a template for any "prove HA exposes the right HAP shape" check:

- Change `REQUIRED_HEATER_COOLER_CHARS` and the two assertion blocks in `run()`
  to the service and characteristics you expect (for example a Switch `49`, a
  Lightbulb `43` or a Sensor).
- Use `--heatercooler-name` / `--thermostat-name` (or the `-aid` variants) to
  select the accessories under test by name substring or accessory id.
- The accessory/service/characteristic dump runs unconditionally, so even
  without assertions it is a quick way to see exactly what a bridge advertises.

## WSL and zeroconf notes

- Discovery over mDNS works from inside WSL2 because a bridge started with
  podman `--network host` advertises on the WSL virtual network, which this
  script shares. Those mDNS records are not visible to devices on the
  Windows-side LAN, but that only affects iOS pairing from a phone, not this
  headless client.
- The `aiohomekit` controller starts both an IP (`_hap._tcp.local.`) and a CoAP
  (`_hap._udp.local.`) transport, so the zeroconf browser must cover both
  service types. The script does this. A browser that only covers `_hap._tcp`
  makes controller start-up raise `TransportNotSupportedError`.
- If mDNS ever proves flaky, `--ip`/`--port` (optionally with
  `--force-fallback`) bypasses discovery entirely. Pairing works over the
  loopback `127.0.0.1` or the host's LAN IP.

## Safety

The script only pairs, reads and unpairs. Point it at a test bridge (see
[`../podman`](../podman)), never at a bridge that fronts real devices you do not
want a controller touching.
