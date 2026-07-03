#!/usr/bin/env python3
"""Headless Home Assistant onboarding and access-token minting.

Creates the owner account on a *fresh* Home Assistant instance via
``/api/onboarding/users`` and exchanges the returned auth code for an OAuth
access token via ``/auth/token``. Use it to obtain a bearer credential for a
throwaway test instance (for example one started by ``../podman/ha-bench.sh``)
so ``driver.py`` can read state and drive mock entities.

Standard library only, so it runs anywhere with no install. It refuses to run
against an instance that has already been onboarded.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8123"
DEFAULT_TIMEOUT = 30.0


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, str]:
    """Perform an HTTP request and return (status, body). No exception on 4xx/5xx."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def _post_json(url: str, payload: dict, timeout: float) -> tuple[int, str]:
    return _request(
        url,
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )


def _post_form(url: str, fields: dict[str, str], timeout: float) -> tuple[int, str]:
    return _request(
        url,
        method="POST",
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )


def _onboarding_steps(base: str, timeout: float) -> dict[str, bool] | None:
    """Return {step: done} from /api/onboarding, or None when unavailable."""
    status, body = _request(f"{base}/api/onboarding", timeout=timeout)
    if status != 200:
        return None
    try:
        steps = json.loads(body)
    except json.JSONDecodeError:
        return None
    return {s["step"]: bool(s.get("done")) for s in steps if "step" in s}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--url", default=DEFAULT_URL, help=f"HA base URL (default {DEFAULT_URL})")
    p.add_argument("--name", default="Test Harness", help="Owner display name")
    p.add_argument("--username", default="harness", help="Owner username")
    p.add_argument("--password", default="harness-password", help="Owner password")
    p.add_argument("--language", default="en", help="UI language (default en)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Per-request timeout in seconds")
    p.add_argument(
        "--finish",
        action="store_true",
        help="Best-effort completion of the remaining onboarding steps (core_config, analytics)",
    )
    p.add_argument("--token-only", action="store_true", help="Print only the access token to stdout")
    return p


def run(args: argparse.Namespace) -> int:
    base = args.url.rstrip("/")
    client_id = f"{base}/"

    steps = _onboarding_steps(base, args.timeout)
    if steps is not None and steps.get("user"):
        print(
            "error: this instance is already onboarded (the 'user' step is done). "
            "Onboarding can only create the owner on a fresh instance.",
            file=sys.stderr,
        )
        return 1

    status, body = _post_json(
        f"{base}/api/onboarding/users",
        {
            "client_id": client_id,
            "name": args.name,
            "username": args.username,
            "password": args.password,
            "language": args.language,
        },
        args.timeout,
    )
    if status != 200:
        print(f"error: POST /api/onboarding/users returned {status}: {body}", file=sys.stderr)
        return 1
    auth_code = json.loads(body).get("auth_code")
    if not auth_code:
        print(f"error: no auth_code in onboarding response: {body}", file=sys.stderr)
        return 1

    status, body = _post_form(
        f"{base}/auth/token",
        {"grant_type": "authorization_code", "code": auth_code, "client_id": client_id},
        args.timeout,
    )
    if status != 200:
        print(f"error: POST /auth/token returned {status}: {body}", file=sys.stderr)
        return 1
    tokens = json.loads(body)
    access = tokens.get("access_token")
    if not access:
        print(f"error: no access_token in token response: {body}", file=sys.stderr)
        return 1

    if args.finish:
        auth_header = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}
        for step in ("core_config", "analytics"):
            s, _ = _request(
                f"{base}/api/onboarding/{step}",
                method="POST",
                data=b"{}",
                headers=auth_header,
                timeout=args.timeout,
            )
            print(f"[onboard] finish {step}: HTTP {s}", file=sys.stderr)

    if args.token_only:
        print(access)
        return 0

    print(
        json.dumps(
            {
                "base_url": base,
                "access_token": access,
                "refresh_token": tokens.get("refresh_token"),
                "token_type": tokens.get("token_type", "Bearer"),
                "expires_in": tokens.get("expires_in"),
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
