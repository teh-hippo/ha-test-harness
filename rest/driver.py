#!/usr/bin/env python3
"""Read-only Home Assistant REST driver, with guarded actuation for test benches.

Reads are always allowed: dump states, read one entity, pull history, show the
config and the service registry. Writing (calling a service) is refused unless
you explicitly opt in with ``--actuate``, and even then it refuses a non-local
Home Assistant unless you also pass ``--allow-remote``. That keeps this tool
read-only against a live Home Assistant by default, while still letting you
drive mock entities on a disposable test instance.

Auth comes from ``--token`` or the ``HA_TOKEN`` environment variable; the base
URL from ``--url`` or ``HA_URL`` (default http://127.0.0.1:8123). Mint a token
for a fresh test instance with ``onboard.py``.

Standard library only, so it runs anywhere with no install.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8123"
DEFAULT_TIMEOUT = 30.0
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _is_local(base: str) -> bool:
    host = urllib.parse.urlparse(base).hostname or ""
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _context(insecure: bool) -> ssl.SSLContext | None:
    if not insecure:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _request(
    url: str,
    token: str | None,
    *,
    method: str = "GET",
    data: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    insecure: bool = False,
) -> tuple[int, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_context(insecure)) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def _emit(status: int, body: str) -> int:
    if not 200 <= status < 300:
        print(f"error: HTTP {status}: {body}", file=sys.stderr)
        return 1
    try:
        print(json.dumps(json.loads(body), indent=2))
    except json.JSONDecodeError:
        print(body)
    return 0


def cmd_states(args: argparse.Namespace) -> int:
    status, body = _request(f"{args.url}/api/states", args.token, timeout=args.timeout, insecure=args.insecure)
    if not 200 <= status < 300:
        print(f"error: HTTP {status}: {body}", file=sys.stderr)
        return 1
    states = json.loads(body)
    if args.domain:
        prefix = f"{args.domain}."
        states = [s for s in states if str(s.get("entity_id", "")).startswith(prefix)]
    print(json.dumps(states, indent=2))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    entity = urllib.parse.quote(args.entity_id)
    status, body = _request(f"{args.url}/api/states/{entity}", args.token, timeout=args.timeout, insecure=args.insecure)
    return _emit(status, body)


def cmd_history(args: argparse.Namespace) -> int:
    query = {"filter_entity_id": args.entity_id}
    if args.end:
        query["end_time"] = args.end
    path = "/api/history/period"
    if args.start:
        path += f"/{urllib.parse.quote(args.start)}"
    url = f"{args.url}{path}?{urllib.parse.urlencode(query)}"
    status, body = _request(url, args.token, timeout=args.timeout, insecure=args.insecure)
    return _emit(status, body)


def cmd_config(args: argparse.Namespace) -> int:
    status, body = _request(f"{args.url}/api/config", args.token, timeout=args.timeout, insecure=args.insecure)
    return _emit(status, body)


def cmd_services(args: argparse.Namespace) -> int:
    status, body = _request(f"{args.url}/api/services", args.token, timeout=args.timeout, insecure=args.insecure)
    return _emit(status, body)


def cmd_call(args: argparse.Namespace) -> int:
    if not args.actuate:
        print(
            "refusing to actuate: 'call' changes state. Re-run with --actuate once "
            "you are sure the target is a disposable test instance with mock entities.",
            file=sys.stderr,
        )
        return 2
    if not _is_local(args.url) and not args.allow_remote:
        print(
            f"refusing to actuate a non-local Home Assistant ({args.url}). This driver is "
            "read-only against a live HA. Pass --allow-remote only if you are certain this "
            "is a disposable test instance.",
            file=sys.stderr,
        )
        return 2

    try:
        payload = json.loads(args.data) if args.data else {}
    except json.JSONDecodeError as err:
        print(f"error: --data is not valid JSON: {err}", file=sys.stderr)
        return 2

    print(
        f"[driver] actuating {args.domain}.{args.service} on {args.url} (data={json.dumps(payload)})",
        file=sys.stderr,
    )
    url = f"{args.url}/api/services/{urllib.parse.quote(args.domain)}/{urllib.parse.quote(args.service)}"
    status, body = _request(
        url,
        args.token,
        method="POST",
        data=json.dumps(payload).encode(),
        timeout=args.timeout,
        insecure=args.insecure,
    )
    return _emit(status, body)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("HA_URL", DEFAULT_URL), help=f"HA base URL (default {DEFAULT_URL})")
    p.add_argument("--token", default=os.environ.get("HA_TOKEN"), help="Bearer token (env HA_TOKEN)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Per-request timeout in seconds")
    p.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification (self-signed HTTPS)")

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("states", help="Dump all states (optionally one domain)")
    sp.add_argument("--domain", help="Only entities in this domain, e.g. climate")
    sp.set_defaults(func=cmd_states)

    sp = sub.add_parser("get", help="Read one entity state")
    sp.add_argument("entity_id")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("history", help="Read history for one entity")
    sp.add_argument("entity_id")
    sp.add_argument("--start", help="ISO8601 start timestamp (path segment)")
    sp.add_argument("--end", help="ISO8601 end timestamp (end_time query)")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("config", help="Read /api/config")
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("services", help="List the service registry")
    sp.set_defaults(func=cmd_services)

    sp = sub.add_parser("call", help="Call a service (guarded; changes state)")
    sp.add_argument("domain")
    sp.add_argument("service")
    sp.add_argument("--data", help='JSON service data, e.g. \'{"entity_id": "climate.mock_daikin"}\'')
    sp.add_argument("--actuate", action="store_true", help="Required opt-in to actually call the service")
    sp.add_argument("--allow-remote", action="store_true", help="Permit actuation against a non-local HA")
    sp.set_defaults(func=cmd_call)

    return p


def main() -> int:
    args = build_parser().parse_args()
    if not args.token:
        print("error: no token. Pass --token or set HA_TOKEN (mint one with onboard.py).", file=sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
