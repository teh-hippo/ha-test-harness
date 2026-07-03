# Mocks: fake devices as Home Assistant entities

A mock is a tiny custom integration that presents synthetic entities with a
full write path, so you can exercise anything downstream of an entity (a
HomeKit bridge, an automation, another integration) without owning or touching
the real device.

`mock_climate` is the first example. Author more of them for other domains
using the same shape.

## Layout and why `custom_components/` is here

```
mocks/
  custom_components/
    mock_climate/        <- the example integration (domain = mock_climate)
      __init__.py
      manifest.json
      climate.py         <- the platform (entities live here)
      config_flow.py
      const.py
      strings.json
```

Home Assistant's loader discovers custom integrations by importing a top-level
package literally named `custom_components` and scanning it. The test config
sets `pythonpath = ["mocks"]` (see the repo `pyproject.toml`), so `mocks/` is on
`sys.path` and `custom_components.mock_climate` imports both in tests and inside
a running Home Assistant. Keep every mock under `mocks/custom_components/`.

## The `mock_climate` example

Three synthetic `climate` entities, each driven entirely by its supported
features, seeded from a real Daikin unit's read-only history (values are fully
synthetic; no device is contacted):

- `climate.mock_daikin`: single setpoint, seven fan modes, no swing; action
  derives from mode alone, mirroring the real unit.
- `climate.mock_dual_swing`: dual setpoint range plus swing.
- `climate.mock_heat_cool_auto`: single setpoint with a `heat_cool` and `auto`
  mode.

Every service (`set_hvac_mode`, `set_temperature`, `set_fan_mode`,
`set_swing_mode`, `turn_on`, `turn_off`) is implemented and writes state back,
so a HomeKit controller or the iOS Home app can drive it end to end. The unit
tests in [`../tests`](../tests) assert the seeded attributes and the write path.

Load it in a running Home Assistant by installing it (see
[`../podman`](../podman)) and adding `mock_climate:` to `configuration.yaml`,
or through the config-flow UI.

## Authoring a new mock

To mock, say, a switch or a sensor:

1. Create `mocks/custom_components/mock_<name>/` with:
   - `manifest.json`: set `domain` to `mock_<name>`, `config_flow: true`,
     `iot_class: local_push`, `version`, and point `documentation` at this repo.
   - `const.py`: `DOMAIN = "mock_<name>"` and `PLATFORMS = [Platform.SWITCH]`
     (or whichever platform).
   - `__init__.py`: the standard `async_setup` (YAML import),
     `async_setup_entry` (forward to platforms) and `async_unload_entry`. Copy
     `mock_climate/__init__.py` and change the imports.
   - `config_flow.py`: a single-instance flow. Copy `mock_climate/config_flow.py`.
   - `<platform>.py`: subclass the platform entity (`SwitchEntity`,
     `SensorEntity`, ...), implement its write methods, and build your synthetic
     entities in `async_setup_entry`.
   - `strings.json`: config-flow strings.
2. Add unit tests under `tests/` that set the integration up via a
   `MockConfigEntry` and assert attributes and the write path. Mirror
   `tests/test_mock_climate.py`.
3. Run the suite: `python -m pytest tests/`.

Keep entities self-contained and synthetic. A mock should never open a socket to
a real device; seed its initial state from saved, read-only history instead.
