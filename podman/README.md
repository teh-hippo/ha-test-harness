# Podman HA bench

Scripts and config to stand up a headless Home Assistant container with your
custom integration installed, so you can drive it end to end without touching
real hardware. Everything works the same with Docker (`--engine docker`).

The bench never contacts a real device. It only runs Home Assistant against the
integrations, mocks and config you install into it.

## Prerequisites

- `podman` (or `docker`) on your PATH.
- Network access to pull `ghcr.io/home-assistant/home-assistant` images.

## Quick start

Stand up a stable Home Assistant with the mock climate integration and a
HomeKit bridge, on host networking so HomeKit/mDNS works:

```bash
cd podman
./ha-bench.sh --name ha-mock --host-net \
    --component ../mocks/custom_components/mock_climate \
    --seed-config config/configuration.example.yaml
```

Then:

- Mint a token headlessly: `python ../rest/onboard.py --url http://127.0.0.1:8123`
  (see [`../rest`](../rest)).
- Read the HomeKit PIN and device id from the log
  (`podman logs -f ha-mock`) or from `<config>/.storage/homekit.*`.
- Run the HAP smoke against the bridge (see [`../homekit`](../homekit)).
- Tear it down: `podman rm -f ha-mock`.

## `ha-bench.sh`

A thin, parameterised wrapper around `podman run`. Nothing is hard-coded to a
particular integration:

| Flag | Purpose |
| --- | --- |
| `--name` | Container name (default `ha-bench`). |
| `--image` | HA image (default `...:stable`; use `...:dev` or `...:beta`). |
| `--config` | Host config directory (default `./ha-config`). |
| `--component PATH` | Copy a `custom_components/<domain>` dir into the config. Repeatable. |
| `--core-overlay SRC:DST` | Bind-mount SRC over image path DST, read-only. Repeatable. |
| `--seed-config FILE` | Copy FILE to `configuration.yaml` when absent. |
| `--port PORT` | Publish 8123 on host PORT (bridge networking). |
| `--host-net` | Use `--network host` (needed for HomeKit/mDNS). |
| `--engine` | `podman` or `docker`. |
| `--recreate` | Remove an existing container of the same name first. |
| `--no-start` | Prepare config and print the run command without starting. |

Run `./ha-bench.sh --help` for the full list.

## Two benches

The harness supports two complementary ways to validate an integration. The
HeaterCooler work these scripts grew out of used both, but the pattern applies
to any integration.

### 1. Custom-component bench (stable image)

Copy a custom integration into the config and let it do its work. This is the
common case: any integration published to HACS or `custom_components/`.

```bash
./ha-bench.sh --name ha-mock --host-net \
    --component ../mocks/custom_components/mock_climate \
    --component /path/to/your_integration \
    --seed-config config/configuration.example.yaml
```

Add your integration's YAML to `configuration.yaml` (or configure it through the
onboarding UI / a config entry).

### 2. Native / patched-core bench (dev image)

Run a patched copy of a built-in component on a matching nightly core, by
bind-mounting your patched source read-only over the image's own path. This
proves a core change without rebuilding the image.

```bash
./ha-bench.sh --name ha-dev --image ghcr.io/home-assistant/home-assistant:dev --host-net \
    --component ../mocks/custom_components/mock_climate \
    --core-overlay ./homekit-patched:/usr/src/homeassistant/homeassistant/components/homekit \
    --seed-config config/configuration.example.yaml
```

Here `./homekit-patched` is a snapshot of your patched
`homeassistant/components/homekit`. Mounting it over the dev image's path runs
the real patch on matching dev core. For the HeaterCooler example the config
opts an entity in natively:

```yaml
homekit:
  - name: Harness Bridge
    port: 21063
    entity_config:
      climate.mock_daikin:
        type: heater_cooler
```

## Config templates

- `config/configuration.example.yaml` is a generic baseline: `default_config`,
  a logger, the mock integration and a HomeKit bridge exposing the mock
  entities. It runs a HomeKit smoke with no patch at all, then you layer your
  integration or a native `entity_config` on top (both shown inline in the
  file).

## Networking notes

- HomeKit discovery needs mDNS, so use `--host-net`. Two host-net containers
  both bind HA's `:8123`, so run one at a time or give each its own
  `http: server_port:` and only rely on the distinct HAP ports.
- Under WSL2 the bridge advertises on the WSL virtual network, which the
  headless HAP smoke shares. A phone on the Windows-side LAN cannot see it
  without `.wslconfig` `networkingMode=mirrored` or an mDNS relay, so pair
  headlessly with the smoke instead.
- For plain REST driving (no HomeKit) prefer `--port 8125` bridge networking to
  avoid `:8123` collisions.

## Safety

- The bench only exercises the integrations and mocks you install. Do not
  install credentials for, or point config at, real devices you do not want
  driven.
- Stop and remove benches you are done with: `podman rm -f <name>`.
