#!/usr/bin/env bash
# Stand up a headless Home Assistant container with one or more custom
# integrations installed, for hardware-free testing. Nothing here contacts a
# real device: it only runs Home Assistant against whatever integrations and
# mocks you install into the config.
set -euo pipefail

NAME="ha-bench"
IMAGE="ghcr.io/home-assistant/home-assistant:stable"
CONFIG="./ha-config"
ENGINE="podman"
PORT=""
HOST_NET=0
RECREATE=0
NO_START=0
SEED_CONFIG=""
COMPONENTS=()
OVERLAYS=()

usage() {
    cat <<'EOF'
Usage: ha-bench.sh [options]

Stand up a headless Home Assistant container with custom integrations installed.

Options:
  --name NAME            Container name (default: ha-bench)
  --image IMAGE          HA image (default: ghcr.io/home-assistant/home-assistant:stable)
                         Use ...:dev for the nightly image, ...:beta for the beta.
  --config DIR           Host config directory (default: ./ha-config)
  --component PATH       Path to a custom_components/<domain> directory to install.
                         Repeatable. Copied into <config>/custom_components/.
  --core-overlay SRC:DST Bind-mount host path SRC over image path DST, read-only.
                         Repeatable. Use it to run a patched core component on a
                         matching :dev image, e.g.
                         --core-overlay ./homekit-patched:/usr/src/homeassistant/homeassistant/components/homekit
  --seed-config FILE     Copy FILE to <config>/configuration.yaml when absent.
  --port PORT            Publish HA's 8123 on host PORT (bridge networking).
  --host-net             Use --network host (needed for HomeKit/mDNS). Cannot be
                         combined with --port.
  --engine ENGINE        Container engine: podman or docker (default: podman).
  --recreate             Stop and remove an existing container of the same name first.
  --no-start             Prepare config and print the run command without starting.
  -h, --help             Show this help.

Examples:
  # Stable HA with the mock climate integration, host networking for HomeKit.
  ./ha-bench.sh --name ha-mock --host-net \
      --component ../mocks/custom_components/mock_climate \
      --seed-config config/configuration.example.yaml

  # Dev image running a patched core component (native-patch bench).
  ./ha-bench.sh --name ha-dev --image ghcr.io/home-assistant/home-assistant:dev --host-net \
      --component ../mocks/custom_components/mock_climate \
      --core-overlay ./homekit-patched:/usr/src/homeassistant/homeassistant/components/homekit \
      --seed-config config/configuration.example.yaml
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) NAME="$2"; shift 2 ;;
        --image) IMAGE="$2"; shift 2 ;;
        --config) CONFIG="$2"; shift 2 ;;
        --component) COMPONENTS+=("$2"); shift 2 ;;
        --core-overlay) OVERLAYS+=("$2"); shift 2 ;;
        --seed-config) SEED_CONFIG="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --host-net) HOST_NET=1; shift ;;
        --engine) ENGINE="$2"; shift 2 ;;
        --recreate) RECREATE=1; shift ;;
        --no-start) NO_START=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ $HOST_NET -eq 1 && -n "$PORT" ]]; then
    echo "error: --host-net and --port are mutually exclusive." >&2
    exit 2
fi

if ! command -v "$ENGINE" >/dev/null 2>&1; then
    echo "error: container engine '$ENGINE' not found on PATH." >&2
    exit 1
fi

mkdir -p "$CONFIG/custom_components"
CONFIG_ABS="$(cd "$CONFIG" && pwd)"

for comp in "${COMPONENTS[@]:-}"; do
    [[ -z "$comp" ]] && continue
    if [[ ! -d "$comp" ]]; then
        echo "error: --component path is not a directory: $comp" >&2
        exit 1
    fi
    domain="$(basename "$comp")"
    rm -rf "${CONFIG_ABS:?}/custom_components/${domain}"
    cp -r "$comp" "$CONFIG_ABS/custom_components/$domain"
    echo "[bench] installed custom_components/$domain"
done

if [[ -n "$SEED_CONFIG" ]]; then
    if [[ ! -f "$SEED_CONFIG" ]]; then
        echo "error: --seed-config file not found: $SEED_CONFIG" >&2
        exit 1
    fi
    if [[ ! -f "$CONFIG_ABS/configuration.yaml" ]]; then
        cp "$SEED_CONFIG" "$CONFIG_ABS/configuration.yaml"
        echo "[bench] seeded configuration.yaml from $SEED_CONFIG"
    else
        echo "[bench] configuration.yaml already present; left untouched"
    fi
fi

if [[ $RECREATE -eq 1 ]]; then
    "$ENGINE" rm -f "$NAME" >/dev/null 2>&1 || true
fi

RUN_ARGS=(run -d --name "$NAME" -v "$CONFIG_ABS:/config")
if [[ $HOST_NET -eq 1 ]]; then
    RUN_ARGS+=(--network host)
elif [[ -n "$PORT" ]]; then
    RUN_ARGS+=(-p "$PORT:8123")
fi
for overlay in "${OVERLAYS[@]:-}"; do
    [[ -z "$overlay" ]] && continue
    src="${overlay%%:*}"
    dst="${overlay#*:}"
    src_abs="$(cd "$(dirname "$src")" && pwd)/$(basename "$src")"
    RUN_ARGS+=(-v "$src_abs:$dst:ro")
    echo "[bench] overlay $src_abs -> $dst (ro)"
done
RUN_ARGS+=("$IMAGE")

if [[ $NO_START -eq 1 ]]; then
    printf '[bench] run command (not started):\n  %s' "$ENGINE"
    printf ' %q' "${RUN_ARGS[@]}"
    printf '\n'
    exit 0
fi

"$ENGINE" "${RUN_ARGS[@]}"

echo
echo "[bench] '$NAME' is starting on image $IMAGE."
echo "[bench] follow the log:      $ENGINE logs -f $NAME"
if [[ $HOST_NET -eq 0 && -n "$PORT" ]]; then
    echo "[bench] onboarding UI:       http://127.0.0.1:$PORT"
fi
echo "[bench] mint a token:        python ../rest/onboard.py --url http://127.0.0.1:${PORT:-8123}"
echo "[bench] find a HomeKit PIN:  read it from the log, or grep -RniE 'pin|homekit' $CONFIG_ABS/.storage/ 2>/dev/null"
echo "[bench] run the HAP smoke:   see ../homekit/README.md"
echo "[bench] stop and remove:     $ENGINE rm -f $NAME"
