#!/usr/bin/env python3
"""Shared plumbing for the docker/*.py bootstrap helpers: a tiny Admin API client
(stdlib ``urllib``), ``docker/.generated.env`` read/upsert, and ``docker exec`` wrappers.

Not a package — the helpers are run as scripts (``python docker/x.py``), which puts this
directory on ``sys.path``.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DEFAULT_CONTAINER = "commerce-agents-shopware"
STOREFRONT_TYPE_ID = "8a243080f92e4c719546314b577cf82b"
CURRENCY_EUR = "b7d2554b0ce847cd82f3ac9bd1c0dfca"
HTTP_TIMEOUT_SECONDS = 60


def new_id() -> str:
    return uuid.uuid4().hex


class AdminApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        super().__init__(f"{method} {path} -> {status}: {detail}")
        self.status = status
        self.detail = detail


class AdminApi:
    """Minimal Shopware Admin API client: password grant for bootstrap, plain JSON."""

    def __init__(self, shop_url: str) -> None:
        self.shop_url = shop_url.rstrip("/")
        self.token: str | None = None

    def login(self, username: str, password: str) -> None:
        body = self.request(
            "POST",
            "/api/oauth/token",
            {
                "grant_type": "password",
                "client_id": "administration",
                "username": username,
                "password": password,
                # acl_role / integration writes go through UserController, which demands
                # the "user-verified" scope (the Admin UI asks for the password again).
                "scope": "write user-verified",
            },
            auth=False,
        )
        self.token = body["access_token"]

    def client_credentials(self, access_key: str, secret_key: str) -> str:
        body = self.request(
            "POST",
            "/api/oauth/token",
            {
                "grant_type": "client_credentials",
                "client_id": access_key,
                "client_secret": secret_key,
            },
            auth=False,
        )
        return str(body["access_token"])

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        auth: bool = True,
        headers: dict[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode()
        request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth:
            request_headers["Authorization"] = f"Bearer {token or self.token}"
        request_headers.update(headers or {})
        req = urllib.request.Request(
            f"{self.shop_url}{path}", data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1200]
            raise AdminApiError(method, path, error.code, detail) from error
        if not raw:
            return {}
        return json.loads(raw)

    def search(self, entity: str, criteria: dict[str, Any]) -> list[dict[str, Any]]:
        body = self.request("POST", f"/api/search/{entity}", criteria)
        return list(body.get("data") or [])

    def search_one(self, entity: str, field: str, value: Any) -> dict[str, Any] | None:
        rows = self.search(
            entity, {"limit": 1, "filter": [{"type": "equals", "field": field, "value": value}]}
        )
        return rows[0] if rows else None

    def count(self, entity: str, filters: list[dict[str, Any]]) -> int:
        body = self.request(
            "POST",
            f"/api/search/{entity}",
            {"limit": 1, "total-count-mode": 1, "filter": filters},
        )
        return int(body.get("total") or 0)

    def storefront_sales_channel(self) -> dict[str, Any]:
        rows = self.search(
            "sales-channel",
            {
                "limit": 1,
                "filter": [{"type": "equals", "field": "typeId", "value": STOREFRONT_TYPE_ID}],
                "associations": {"domains": {}},
            },
        )
        if not rows:
            raise RuntimeError("no Storefront sales channel found")
        return rows[0]


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def upsert_env_file(path: Path, values: dict[str, str], *, remove: tuple[str, ...] = ()) -> None:
    """Rewrite ``path`` keeping unknown keys and comments, replacing/adding ``values``
    and dropping ``remove``. The file is created when missing."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(values)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in remove:
                continue
            if key in pending:
                output.append(f"{key}={pending.pop(key)}")
                continue
        output.append(line)
    for key, value in pending.items():
        output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip("\n") + "\n", encoding="utf-8")


def docker_exec(
    container: str, command: str, *, user: str = "www-data"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", "-u", user, container, "bash", "-lc", command],
        check=False,
        capture_output=True,
        text=True,
    )


def console(container: str, args: str) -> subprocess.CompletedProcess[str]:
    return docker_exec(container, f"cd /var/www/html && php bin/console {args}")
