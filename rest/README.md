# REST driver and headless onboarding

Two standard-library helpers (no install) for driving Home Assistant over its
REST API: mint a token on a fresh instance, then read state and, on a test
bench only, drive entities.

## `onboard.py`: mint a token on a fresh instance

On a freshly started Home Assistant (for example one from
[`../podman`](../podman)), create the owner account and exchange the resulting
auth code for an access token, all headlessly:

```bash
python onboard.py --url http://127.0.0.1:8123
```

It prints the base URL, access token, refresh token and expiry as JSON. Use
`--token-only` to print just the token (handy for `export HA_TOKEN=$(...)`), and
`--finish` to best-effort complete the remaining onboarding steps. It refuses to
run if the instance has already been onboarded.

The flow is exactly the two documented onboarding calls: `POST
/api/onboarding/users` then `POST /auth/token`.

## `driver.py`: read-only client with guarded actuation

```bash
export HA_URL=http://127.0.0.1:8123
export HA_TOKEN=$(python onboard.py --url "$HA_URL" --token-only)

python driver.py states --domain climate       # all climate states
python driver.py get climate.mock_daikin        # one entity
python driver.py history climate.mock_daikin --start 2026-01-01T00:00:00+00:00
python driver.py config                          # /api/config
python driver.py services                        # the service registry
```

Reads are always allowed. Calling a service changes state, so it is guarded:

```bash
# Refused without --actuate:
python driver.py call climate set_temperature \
    --data '{"entity_id": "climate.mock_daikin", "temperature": 21}'

# Allowed against a local test instance:
python driver.py call climate set_temperature --actuate \
    --data '{"entity_id": "climate.mock_daikin", "temperature": 21}'
```

`call` requires `--actuate`, and refuses a non-local Home Assistant unless you
also pass `--allow-remote`. Use `--insecure` for a self-signed HTTPS bench.

## Safety

- Default behaviour is read-only, which is the only mode you should ever point
  at a live Home Assistant that fronts real devices.
- Only actuate (`--actuate`) against a disposable test instance running the
  mocks. The non-local guard exists to stop an accidental write to production.
- Read history and states against a live instance freely; those endpoints never
  change device state.
