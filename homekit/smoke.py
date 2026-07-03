#!/usr/bin/env python3
"""Reusable headless HomeKit verification for the HeaterCooler patch.

This script drives an ``aiohomekit`` IP-transport client against a running
Home Assistant HomeKit bridge and proves that a routed ``climate`` entity is
exposed as a native HeaterCooler accessory (HAP service ``BC``) while an
un-routed climate entity stays a Thermostat (HAP service ``4A``).

It is deliberately parameterised (device id, PIN, optional IP:port and the
accessory-name substrings used for the assertions) so the very same script can
be re-run against a different bridge later, e.g. the patched-core instance.

Discovery prefers zeroconf (mDNS) and transparently falls back to a directly
constructed IP:port connection when mDNS is unavailable (handy under WSL).

Exit code is ``0`` only when every assertion passes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

from aiohomekit import Controller
from aiohomekit.characteristic_cache import CharacteristicCacheMemory
from aiohomekit.controller.abstract import AbstractPairing, TransportType
from aiohomekit.controller.ip.discovery import IpDiscovery
from aiohomekit.exceptions import (
    AccessoryNotFoundError,
    HomeKitException,
    UnavailableError,
)
from aiohomekit.model.categories import Categories
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.model.feature_flags import FeatureFlags
from aiohomekit.model.services import ServicesTypes
from aiohomekit.model.status_flags import StatusFlags
from aiohomekit.uuid import normalize_uuid, shorten_uuid
from aiohomekit.zeroconf import HAP_TYPE_TCP, HomeKitService, ZeroconfServiceListener
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

# --- HAP type constants (already full, upper-case UUIDs in the library) -------
SVC_ACCESSORY_INFO = ServicesTypes.ACCESSORY_INFORMATION  # 3E
SVC_HEATER_COOLER = ServicesTypes.HEATER_COOLER  # BC
SVC_THERMOSTAT = ServicesTypes.THERMOSTAT  # 4A

CHAR_NAME = CharacteristicsTypes.NAME  # 23

# Characteristics that a native HeaterCooler accessory must expose.
REQUIRED_HEATER_COOLER_CHARS: dict[str, str] = {
    "Active": CharacteristicsTypes.ACTIVE,  # B0
    "CurrentHeaterCoolerState": CharacteristicsTypes.CURRENT_HEATER_COOLER_STATE,  # B1
    "TargetHeaterCoolerState": CharacteristicsTypes.TARGET_HEATER_COOLER_STATE,  # B2
    "CoolingThresholdTemperature": CharacteristicsTypes.TEMPERATURE_COOLING_THRESHOLD,  # 0D
    "HeatingThresholdTemperature": CharacteristicsTypes.TEMPERATURE_HEATING_THRESHOLD,  # 12
    "RotationSpeed": CharacteristicsTypes.ROTATION_SPEED,  # 29
}


def _reverse_type_names(cls) -> dict[str, str]:
    """Build a {normalised-uuid: FRIENDLY_NAME} map from a *Types class."""
    out: dict[str, str] = {}
    for key, value in vars(cls).items():
        if key.startswith("_") or not isinstance(value, str):
            continue
        try:
            out[normalize_uuid(value)] = key
        except ValueError:
            continue
    return out


SERVICE_NAMES = _reverse_type_names(ServicesTypes)
CHAR_NAMES = _reverse_type_names(CharacteristicsTypes)


def svc_label(type_uuid: str) -> str:
    norm = normalize_uuid(type_uuid)
    return f"{shorten_uuid(norm)} ({SERVICE_NAMES.get(norm, 'UNKNOWN')})"


def char_label(type_uuid: str) -> str:
    norm = normalize_uuid(type_uuid)
    return f"{shorten_uuid(norm)} ({CHAR_NAMES.get(norm, 'UNKNOWN')})"


# --- Parsed accessory view ----------------------------------------------------
@dataclass
class AccessoryView:
    aid: int
    name: str
    service_types: set[str]  # normalised uuids
    # {normalised service uuid: {normalised char uuid: (iid, value)}}
    services: dict[str, dict[str, tuple[int, object]]]


def parse_accessories(raw: list[dict]) -> list[AccessoryView]:
    views: list[AccessoryView] = []
    for accessory in raw:
        aid = accessory["aid"]
        name = ""
        service_types: set[str] = set()
        services: dict[str, dict[str, tuple[int, object]]] = {}
        for service in accessory["services"]:
            s_type = normalize_uuid(service["type"])
            service_types.add(s_type)
            chars: dict[str, tuple[int, object]] = {}
            for char in service["characteristics"]:
                c_type = normalize_uuid(char["type"])
                value = char.get("value")
                chars[c_type] = (char["iid"], value)
                if s_type == SVC_ACCESSORY_INFO and c_type == CHAR_NAME and value:
                    name = str(value)
            services[s_type] = chars
        views.append(AccessoryView(aid, name, service_types, services))
    return views


def dump_accessories(raw: list[dict]) -> None:
    print("=" * 78)
    print("ACCESSORY / SERVICE / CHARACTERISTIC DUMP")
    print("=" * 78)
    for accessory in raw:
        aid = accessory["aid"]
        # Find a friendly name for the header.
        name = ""
        for service in accessory["services"]:
            if normalize_uuid(service["type"]) != SVC_ACCESSORY_INFO:
                continue
            for char in service["characteristics"]:
                if normalize_uuid(char["type"]) == CHAR_NAME and char.get("value"):
                    name = str(char["value"])
        print(f"\nAccessory aid={aid}  name={name!r}")
        for service in accessory["services"]:
            print(f"  Service  iid={service['iid']:<4} type={svc_label(service['type'])}")
            for char in service["characteristics"]:
                perms = ",".join(char.get("perms", []))
                value = char.get("value", "<no-value>")
                print(
                    f"    Char   iid={char['iid']:<4} type={char_label(char['type'])} value={value!r} perms=[{perms}]"
                )
    print()


def find_accessory(
    accessories: list[AccessoryView],
    name_substring: str,
    aid_override: int | None,
    role: str,
) -> AccessoryView | None:
    if aid_override is not None:
        for acc in accessories:
            if acc.aid == aid_override:
                return acc
        print(f"  [!] No accessory with aid={aid_override} for {role}.")
        return None

    needle = name_substring.lower()
    matches = [a for a in accessories if needle in a.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        print(f"  [!] No accessory whose name contains {name_substring!r} for {role}.")
    else:
        aids = ", ".join(str(a.aid) for a in matches)
        print(f"  [!] Ambiguous: multiple accessories match {name_substring!r} for {role} (aids: {aids}).")
    return None


# --- Discovery ----------------------------------------------------------------
async def discover_bridge(
    controller: Controller,
    device_id: str,
    ip: str | None,
    port: int,
    timeout: float,
    force_fallback: bool,
) -> tuple[IpDiscovery, str, bool]:
    """Return (discovery, method, sf_trusted) using zeroconf first, IP:port as
    fallback. ``sf_trusted`` is True only for zeroconf results; the fallback has
    no mDNS TXT record, so its status flags are a placeholder and pair-setup is
    the real source of truth for the paired/unpaired state."""
    if not force_fallback:
        try:
            discovery = await controller.async_find(device_id, timeout=timeout)
            return discovery, "zeroconf", True
        except AccessoryNotFoundError as err:
            if not ip:
                raise
            print(f"[discover] zeroconf failed ({err}); falling back to {ip}:{port}")

    if not ip:
        raise AccessoryNotFoundError(f"Device {device_id} not found via zeroconf and no --ip fallback provided")

    ip_controller = controller.transports[TransportType.IP]
    service = HomeKitService(
        name=f"bridge-{device_id.replace(':', '')}",
        id=device_id.lower(),
        model="",
        feature_flags=FeatureFlags(0),
        # No mDNS TXT available in the fallback: assume unpaired and let
        # pair-setup fail loudly if the bridge is actually already paired.
        status_flags=StatusFlags.UNPAIRED,
        config_num=0,
        state_num=0,
        category=Categories(2),
        protocol_version="1.0",
        type=HAP_TYPE_TCP,
        address=ip,
        addresses=[ip],
        port=port,
    )
    return IpDiscovery(ip_controller, service), f"ip-fallback ({ip}:{port})", False


def register_pairing(controller: Controller, alias: str, pairing: AbstractPairing) -> None:
    """finish_pairing() only registers on the transport; mirror it on the top
    level controller so save_data() and remove_pairing() work by alias."""
    controller.aliases[alias] = pairing
    controller.pairings[pairing.id] = pairing


async def confirm_repairable(
    controller: Controller,
    device_id: str,
    ip: str | None,
    port: int,
    timeout: float,
    force_fallback: bool,
) -> bool:
    """Prove the bridge accepts a fresh pairing by starting pair-setup (M1->M2)
    and then abandoning it before M3, so no persistent pairing is created."""
    try:
        discovery, method, _ = await discover_bridge(controller, device_id, ip, port, timeout, force_fallback)
    except AccessoryNotFoundError as err:
        print(f"  [!] Could not rediscover bridge to confirm re-pairability: {err}")
        return False

    try:
        await discovery.async_start_pairing("repair-probe")
        print(f"  [ok] pair-setup M1->M2 accepted via {method}: bridge is re-pairable (probe aborted before M3).")
        return True
    except UnavailableError:
        print("  [!] pair-setup returned Unavailable: bridge still appears paired.")
        return False
    except HomeKitException as err:
        print(f"  [!] Re-pairability probe failed: {err}")
        return False
    finally:
        try:
            await discovery.close()
        except Exception:  # noqa: S110, BLE001 - best-effort cleanup
            pass


# --- Main flow ----------------------------------------------------------------
async def run(args: argparse.Namespace) -> int:
    device_id = args.device_id
    alias = args.alias
    failures: list[str] = []

    azc = AsyncZeroconf()
    controller = Controller(async_zeroconf_instance=azc, char_cache=CharacteristicCacheMemory())

    print("#" * 78)
    print("aiohomekit HeaterCooler smoke test")
    print(f"  device-id      : {device_id}")
    print(f"  ip:port        : {args.ip or '(zeroconf)'}:{args.port}")
    print(f"  alias          : {alias}")
    print(f"  pairing-file   : {args.pairing_file}")
    print(f"  heatercooler ~ : {args.heatercooler_name!r}  thermostat ~ : {args.thermostat_name!r}")
    print(f"  keep-pairing   : {args.keep_pairing}")
    print("#" * 78)

    async with azc:
        listener = ZeroconfServiceListener()
        browser = AsyncServiceBrowser(azc.zeroconf, [HAP_TYPE_TCP, "_hap._udp.local."], listener=listener)
        async with controller:
            controller.load_data(args.pairing_file)

            pairing: AbstractPairing | None = None
            reused = False
            if alias in controller.aliases:
                pairing = controller.aliases[alias]
                print(f"[pair] Reusing persisted pairing for alias {alias!r}.")
                reused = True

            if pairing is None:
                try:
                    discovery, method, sf_trusted = await discover_bridge(
                        controller,
                        device_id,
                        args.ip,
                        args.port,
                        args.discovery_timeout,
                        args.force_fallback,
                    )
                except AccessoryNotFoundError as err:
                    print(f"[FAIL] Discovery failed: {err}")
                    await browser.async_cancel()
                    return 2

                desc = discovery.description
                sf_display = str(int(desc.status_flags)) if sf_trusted else "unknown (ip-fallback)"
                paired_display = discovery.paired if sf_trusted else "unknown (ip-fallback)"
                print(
                    f"[discover] found via {method}: name={desc.name!r} id={desc.id} "
                    f"addr={desc.address}:{desc.port} sf={sf_display} "
                    f"ff={int(desc.feature_flags)} paired={paired_display}"
                )

                # Only trust the paired flag when it came from a real mDNS TXT
                # record; in the IP fallback pair-setup itself detects an
                # already-paired bridge (raises Unavailable).
                if sf_trusted and discovery.paired:
                    print(
                        "[FAIL] Bridge reports it is already paired but no local pairing "
                        "data was found. It must be unpaired (sf=1) before this script "
                        "can pair. Restart/reset the bridge or supply its pairing file."
                    )
                    await browser.async_cancel()
                    return 2

                try:
                    print("[pair] Starting pair-setup with PIN...")
                    finish_pairing = await discovery.async_start_pairing(alias)
                    pairing = await finish_pairing(args.pin)
                    register_pairing(controller, alias, pairing)
                    controller.save_data(args.pairing_file)
                    print(f"[pair] Paired OK. Pairing persisted to {args.pairing_file}")
                except HomeKitException as err:
                    print(f"[FAIL] Pairing failed: {err}")
                    await browser.async_cancel()
                    return 2

            # --- Read the full accessory database ---
            try:
                raw = await pairing.list_accessories_and_characteristics()
            except HomeKitException as err:
                print(f"[FAIL] Could not read /accessories: {err}")
                if not reused:
                    await _safe_remove(controller, alias, args)
                await browser.async_cancel()
                return 2

            dump_accessories(raw)
            accessories = parse_accessories(raw)

            # --- Assertions ---
            print("=" * 78)
            print("ASSERTIONS")
            print("=" * 78)

            hc_acc = find_accessory(accessories, args.heatercooler_name, args.heatercooler_aid, "HeaterCooler entity")
            th_acc = find_accessory(accessories, args.thermostat_name, args.thermostat_aid, "Thermostat entity")

            # 1) HeaterCooler accessory exposes HeaterCooler (BC) and not Thermostat.
            if hc_acc is None:
                failures.append("HeaterCooler accessory not identified")
            else:
                print(f"\n[HeaterCooler target] aid={hc_acc.aid} name={hc_acc.name!r}")
                if SVC_HEATER_COOLER in hc_acc.service_types:
                    print(f"  [ok] exposes HeaterCooler service ({svc_label(SVC_HEATER_COOLER)})")
                else:
                    failures.append(f"{hc_acc.name!r} does NOT expose HeaterCooler (BC)")
                    print("  [FAIL] missing HeaterCooler service (BC)")

                if SVC_THERMOSTAT in hc_acc.service_types:
                    failures.append(f"{hc_acc.name!r} unexpectedly exposes Thermostat (4A)")
                    print("  [FAIL] unexpectedly exposes Thermostat service (4A)")
                else:
                    print("  [ok] does NOT expose Thermostat service (routing scoped correctly)")

                # Required characteristics + live values.
                hc_chars = hc_acc.services.get(SVC_HEATER_COOLER, {})
                missing = [n for n, u in REQUIRED_HEATER_COOLER_CHARS.items() if u not in hc_chars]
                if missing:
                    failures.append(f"HeaterCooler missing characteristics: {', '.join(missing)}")
                    print(f"  [FAIL] missing characteristics: {', '.join(missing)}")
                else:
                    print("  [ok] all required HeaterCooler characteristics present.")

                # Live read of the required characteristics that are present.
                to_read = [(hc_acc.aid, hc_chars[u][0]) for u in REQUIRED_HEATER_COOLER_CHARS.values() if u in hc_chars]
                live: dict[tuple[int, int], dict] = {}
                if to_read:
                    try:
                        live = await pairing.get_characteristics(to_read)
                    except HomeKitException as err:
                        print(f"  [warn] live characteristic read failed: {err}")

                print("  HeaterCooler characteristic values (live read):")
                for cname, cuuid in REQUIRED_HEATER_COOLER_CHARS.items():
                    if cuuid not in hc_chars:
                        print(f"    - {cname:<28} : <MISSING>")
                        continue
                    iid, dump_val = hc_chars[cuuid]
                    val = live.get((hc_acc.aid, iid), {}).get("value", dump_val)
                    print(f"    - {cname:<28} : {val!r}  (iid={iid}, {char_label(cuuid)})")

            # 2) Thermostat accessory exposes Thermostat (4A) and not HeaterCooler.
            if th_acc is None:
                failures.append("Thermostat accessory not identified")
            else:
                print(f"\n[Thermostat target] aid={th_acc.aid} name={th_acc.name!r}")
                if SVC_THERMOSTAT in th_acc.service_types:
                    print(f"  [ok] exposes Thermostat service ({svc_label(SVC_THERMOSTAT)})")
                else:
                    failures.append(f"{th_acc.name!r} does NOT expose Thermostat (4A)")
                    print("  [FAIL] missing Thermostat service (4A)")

                if SVC_HEATER_COOLER in th_acc.service_types:
                    failures.append(f"{th_acc.name!r} unexpectedly exposes HeaterCooler (BC)")
                    print("  [FAIL] unexpectedly exposes HeaterCooler service (BC)")
                else:
                    print("  [ok] does NOT expose HeaterCooler service (proves routing is opt-in)")

            # --- Teardown: remove pairing so the bridge returns to unpaired ---
            repairable = None
            if args.keep_pairing:
                print(f"\n[teardown] --keep-pairing set; leaving pairing in place ({args.pairing_file}).")
            else:
                print("\n[teardown] Removing pairing so the bridge returns to unpaired (sf=1)...")
                removed = await _safe_remove(controller, alias, args)
                if removed:
                    repairable = await confirm_repairable(
                        controller,
                        device_id,
                        args.ip,
                        args.port,
                        args.discovery_timeout,
                        args.force_fallback,
                    )
                else:
                    failures.append("remove_pairing failed")

        await browser.async_cancel()

    # --- Summary ---
    print("\n" + "=" * 78)
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print(f"  - {f}")
        print("=" * 78)
        return 1

    print("RESULT: PASS")
    print("  - HeaterCooler entity is a native HeaterCooler accessory (BC) with all required characteristics.")
    print("  - Thermostat entity remains a Thermostat (4A); routing is scoped correctly.")
    if not args.keep_pairing:
        print(f"  - Pairing removed; bridge re-pairable: {repairable}")
    print("=" * 78)
    return 0


async def _safe_remove(controller: Controller, alias: str, args: argparse.Namespace) -> bool:
    try:
        await controller.remove_pairing(alias)
        # Persist the now-empty alias set and drop the stale pairing file.
        controller.save_data(args.pairing_file)
        try:
            if os.path.exists(args.pairing_file):
                os.remove(args.pairing_file)
        except OSError:
            pass
        print("  [ok] remove_pairing completed; bridge pairing deleted on both ends.")
        return True
    except HomeKitException as err:
        print(f"  [!] remove_pairing failed: {err}")
        return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Headless aiohomekit HeaterCooler verification for a HA HomeKit bridge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Re-running against a DIFFERENT bridge (e.g. the patched-core instance):\n"
            "  Every target detail is a flag (or env var), so point the same script at a\n"
            "  new bridge by swapping the device-id, PIN and (optionally) IP:port:\n\n"
            "  python homekit/smoke.py \\\n"
            "      --device-id <NEW_HAP_ID> --pin <NEW_PIN> \\\n"
            "      --ip <NEW_IP> --port <NEW_PORT>\n\n"
            "  Or via environment variables:\n"
            "  HC_DEVICE_ID=<id> HC_PIN=<pin> HC_IP=<ip> HC_PORT=<port> \\\n"
            "      .venv/bin/python smoke.py\n\n"
            "  Useful extras:\n"
            "    --force-fallback         skip mDNS, connect straight to --ip:--port\n"
            "    --keep-pairing           leave the pairing in place for reuse/iOS\n"
            "    --heatercooler-name STR  name substring for the routed climate entity\n"
            "    --thermostat-name STR    name substring for the un-routed climate entity\n"
            "    --heatercooler-aid N     pin the HeaterCooler accessory by aid instead\n"
            "    --thermostat-aid N       pin the Thermostat accessory by aid instead\n\n"
            "  The script pairs, dumps every accessory/service/characteristic, asserts the\n"
            "  routed entity is a HeaterCooler (BC) and the un-routed one stays a Thermostat\n"
            "  (4A), then removes the pairing so the bridge returns to sf=1 (unpaired).\n"
            "  Exit code is 0 only when all assertions pass.\n"
        ),
    )
    p.add_argument(
        "--device-id",
        default=os.environ.get("HC_DEVICE_ID"),
        help="HAP device id, e.g. XX:XX:XX:XX:XX:XX (env HC_DEVICE_ID)",
    )
    p.add_argument("--pin", default=os.environ.get("HC_PIN"), help="Pairing PIN in XXX-XX-XXX form (env HC_PIN)")
    p.add_argument("--ip", default=os.environ.get("HC_IP"), help="Bridge IP for the no-mDNS fallback (env HC_IP)")
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("HC_PORT", "21063")),
        help="Bridge HAP port for the fallback (default 21063, env HC_PORT)",
    )
    p.add_argument(
        "--alias", default=os.environ.get("HC_ALIAS", "hc-smoke"), help="Local alias for the pairing (env HC_ALIAS)"
    )
    p.add_argument(
        "--pairing-file",
        default=os.environ.get(
            "HC_PAIRING_FILE",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "pairings", "smoke-pairing.json"),
        ),
        help="Where to persist/reuse pairing data (env HC_PAIRING_FILE)",
    )
    p.add_argument(
        "--discovery-timeout",
        type=float,
        default=float(os.environ.get("HC_DISCOVERY_TIMEOUT", "15")),
        help="Zeroconf discovery timeout in seconds (default 15)",
    )
    p.add_argument("--force-fallback", action="store_true", help="Skip zeroconf and connect straight to --ip:--port")
    p.add_argument(
        "--heatercooler-name",
        default=os.environ.get("HC_HEATERCOOLER_NAME", "daikin"),
        help="Case-insensitive name substring identifying the HeaterCooler entity",
    )
    p.add_argument(
        "--thermostat-name",
        default=os.environ.get("HC_THERMOSTAT_NAME", "dual"),
        help="Case-insensitive name substring identifying the Thermostat entity",
    )
    p.add_argument(
        "--heatercooler-aid",
        type=int,
        default=None,
        help="Optional explicit aid for the HeaterCooler entity (overrides name match)",
    )
    p.add_argument(
        "--thermostat-aid",
        type=int,
        default=None,
        help="Optional explicit aid for the Thermostat entity (overrides name match)",
    )
    p.add_argument(
        "--keep-pairing", action="store_true", help="Do not remove the pairing at the end (persist for reuse)"
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not args.device_id or not args.pin:
        print("error: --device-id and --pin are required (or set HC_DEVICE_ID / HC_PIN).", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
