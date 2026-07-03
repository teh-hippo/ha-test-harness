# ha-test-harness

A reusable, hardware-free test harness for Home Assistant custom integrations.
It lets you validate an integration end to end (state, services, HomeKit, REST)
against synthetic devices and disposable Home Assistant containers, so you never
have to touch, or risk, real hardware.

It grew out of work on a HomeKit HeaterCooler integration, so the shipped
examples are climate and HomeKit flavoured, but every piece is parameterised to
work against any integration (foxess, meross_lan, porkbun_ddns, and so on).

## The four pieces

1. **Mocks** ([`mocks/`](mocks)): a fake device presented as a Home Assistant
   entity with a full write path. `mock_climate` is the first example; the
   README shows how to author a mock for another domain.
2. **Podman HA bench** ([`podman/`](podman)): scripts and config templates to
   stand up a stable or dev Home Assistant container with an arbitrary custom
   component installed, driven headlessly.
3. **HomeKit smoke** ([`homekit/`](homekit)): a headless `aiohomekit`
   controller that pairs, dumps the accessory database, asserts characteristics
   and unpairs, so you can verify HomeKit behaviour with no phone.
4. **REST driver and onboarding** ([`rest/`](rest)): mint a token on a fresh
   Home Assistant via `/api/onboarding/users` and `/auth/token`, then read state
   and drive mock entities over REST. Read-only by default.

## Layout

```
ha-test-harness/
  mocks/
    custom_components/mock_climate/   the example mock integration
    README.md                         how to author a new mock
  podman/
    ha-bench.sh                       parameterised HA container launcher
    config/configuration.example.yaml generic bench config
    README.md
  homekit/
    smoke.py                          parameterised HAP verifier
    README.md
  rest/
    onboard.py                        headless onboarding + token mint
    driver.py                         read-only REST client, guarded actuation
    README.md
  tests/                              the mock's unit tests (run in CI)
  pyproject.toml  .ruff.toml  .github/workflows/validate.yml
```

## Use it against any integration

1. Stand up a bench with your integration and the mock installed:

   ```bash
   cd podman
   ./ha-bench.sh --name ha-test --host-net \
       --component ../mocks/custom_components/mock_climate \
       --component /path/to/your_integration \
       --seed-config config/configuration.example.yaml
   ```

2. Mint a token and drive it over REST:

   ```bash
   export HA_URL=http://127.0.0.1:8123
   export HA_TOKEN=$(python ../rest/onboard.py --url "$HA_URL" --token-only)
   python ../rest/driver.py states --domain climate
   ```

3. If your integration exposes something to HomeKit, verify it headlessly with
   the HAP smoke (see [`homekit/`](homekit)); adapt its two assertion blocks to
   the service and characteristics you expect.

4. Add unit tests for any new mock under [`tests/`](tests) and run them:

   ```bash
   python -m pytest tests/
   ```

## Development

Dependencies and tooling follow the same conventions as the owner's
integrations: Python 3.14, [uv](https://docs.astral.sh/uv/) for dependencies and
[ruff](https://docs.astral.sh/ruff/) for linting.

```bash
uv sync --locked
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync coverage run -m pytest tests/
uv run --no-sync python -m compileall mocks homekit rest
```

CI ([`.github/workflows/validate.yml`](.github/workflows/validate.yml)) runs the
mock unit tests, ruff (check and format) and `compileall` on every push and pull
request.

The `aiohomekit` client in `homekit/` needs its own environment, since
`aiohomekit` is not a harness dependency: `cd homekit && uv venv && uv pip
install aiohomekit`.

## Safety

This harness exists so you never test against real hardware. Two rules keep it
that way:

- **Never actuate real devices.** The mocks and benches are self-contained and
  synthetic. Do not install real-device credentials into a bench you use for
  destructive testing.
- **Read-only against a live Home Assistant.** The REST driver is read-only by
  default and refuses to actuate a non-local instance without an explicit
  override. Reading states and history never changes device state; use that
  freely, but never point actuation at production.

## License

MIT. See [LICENSE](LICENSE).
