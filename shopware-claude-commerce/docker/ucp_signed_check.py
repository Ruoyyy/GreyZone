#!/usr/bin/env python3
"""Prove the shop enforces ``signaturePolicy=strict`` against our published agent key.

    python docker/ucp_signed_check.py --shop-url http://localhost:8080 \
        --pem secrets/ucp-agent-signing-key.pem [--profile-url http://localhost/agent-profile.json]

Sends ``POST /ucp/v1/catalog/search`` twice: RFC 9421-signed with the agent key (expects
200) and unsigned (expects 401). Exit 1 on any mismatch. Needs the repo venv.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from shopware_common.http_signing import RequestSigner  # noqa: E402

DEFAULT_PROFILE_URL = "http://localhost/agent-profile.json"
HTTP_TIMEOUT_SECONDS = 30.0
EXPECTED_SIGNED_STATUS = 200
EXPECTED_UNSIGNED_STATUS = 401


def base_headers(profile_url: str) -> dict[str, str]:
    return {
        "UCP-Agent": f'platform; profile="{profile_url}"',
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--pem", required=True, type=Path)
    parser.add_argument("--profile-url", default=DEFAULT_PROFILE_URL)
    parser.add_argument("--query", default="shirt")
    args = parser.parse_args()

    url = f"{args.shop_url.rstrip('/')}/ucp/v1/catalog/search"
    body = json.dumps({"query": args.query, "limit": 1}).encode()
    signer = RequestSigner.from_pem_file(args.pem)

    failures = 0
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http:
        signed = http.post(
            url,
            content=body,
            headers={**base_headers(args.profile_url), **signer.headers_for("POST", url, body)},
        )
        status = "ok" if signed.status_code == EXPECTED_SIGNED_STATUS else "FAIL"
        print(f"signed request (kid={signer.kid}): {signed.status_code} [{status}]")
        if signed.status_code != EXPECTED_SIGNED_STATUS:
            failures += 1
            print(f"  {signed.text[:300]}", file=sys.stderr)

        unsigned = http.post(url, content=body, headers=base_headers(args.profile_url))
        status = "ok" if unsigned.status_code == EXPECTED_UNSIGNED_STATUS else "FAIL"
        print(f"unsigned request: {unsigned.status_code} [{status}] (strict policy must refuse)")
        if unsigned.status_code != EXPECTED_UNSIGNED_STATUS:
            failures += 1
            print(f"  {unsigned.text[:300]}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
