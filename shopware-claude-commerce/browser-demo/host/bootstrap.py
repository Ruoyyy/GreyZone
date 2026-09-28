# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""In-browser agent host: runs the repo's FastAPI apps (``storefront.api.main`` and
``merchant.api.main``) unchanged inside Pyodide.

Loaded by host/worker.ts into a Web Worker after the wheels are installed and the synced
repo tree is unpacked at ``/repo``. Three responsibilities:

1. **HTTP for httpx.** Pyodide has no sockets; every ``httpx.AsyncClient`` in the process
   (the Anthropic SDK, ``UcpClient``, ``StoreApiClient``, the Admin MCP transport) gets a
   transport backed by the browser: requests to the shop origin are handed to the page,
   which runs them on the PHP WASM worker (``demo_bridge.shop_request``); everything else
   goes through ``fetch`` with a streaming body (SSE from the Messages API works).
2. **ASGI in-process.** ``HostApp`` drives the FastAPI app like a server would: one
   lifespan startup, then ``handle()`` per request, streaming response chunks back to JS
   as they are produced (the chat turn is Server-Sent Events).
3. **Environment.** The same variables the Docker stack writes to ``docker/.generated.env``
   are set from the shell's shop-config.json before the app modules import.

Nothing here modifies the blueprint packages or the repo backends (ADR-1).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import traceback
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("browser_demo.host")

SHOP_HOST_HEADER_SKIP = frozenset({"host", "content-length", "connection", "accept-encoding", "transfer-encoding"})
RESPONSE_HEADER_SKIP = frozenset({"content-encoding", "content-length", "transfer-encoding"})
ANTHROPIC_HOST = "api.anthropic.com"
DIRECT_BROWSER_ACCESS_HEADER = "anthropic-dangerous-direct-browser-access"
ASGI_CLIENT = ("127.0.0.1", 40000)
ASGI_SERVER = ("localhost", 80)
LIFESPAN_TIMEOUT_S = 240

# ------------------------------------------------------------------------------- bridge

try:
    import demo_bridge  # registered by worker.ts (pyodide.registerJsModule)
except ImportError:  # pragma: no cover - only when imported outside Pyodide
    demo_bridge = None  # type: ignore[assignment]

from js import Headers as JsHeaders  # noqa: E402
from js import Object as JsObject  # noqa: E402
from js import fetch as js_fetch  # noqa: E402
from pyodide.ffi import JsException, to_js  # noqa: E402


def _shop_origin() -> str:
    return str(demo_bridge.shopOrigin) if demo_bridge is not None else ""


def _netloc(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value or "")


def _is_shop_url(url: httpx.URL) -> bool:
    """Same host as the WASM shop. The Pages path prefix is restored in the JS
    bridge — matching host-only here still routes a dropped-prefix URL to PHP."""
    origin = _shop_origin()
    if not origin:
        return False
    parts = urlsplit(origin)
    return url.scheme == parts.scheme and _netloc(url.netloc) == _netloc(parts.netloc)


class _ReadableStreamBody(httpx.AsyncByteStream):
    """Adapts a WHATWG ReadableStream reader to httpx's async byte stream."""

    def __init__(self, body: Any) -> None:
        self._reader = body.getReader() if body is not None else None

    async def __aiter__(self):
        if self._reader is None:
            return
        while True:
            chunk = await self._reader.read()
            if chunk.done:
                break
            yield bytes(chunk.value.to_py())

    async def aclose(self) -> None:
        if self._reader is not None:
            with contextlib.suppress(Exception):  # pragma: no cover - reader already released
                await self._reader.cancel()


class _BytesBody(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        yield self._data

    async def aclose(self) -> None:
        return None


async def _request_body(request: httpx.Request) -> bytes:
    stream = request.stream
    if hasattr(stream, "__aiter__"):
        return b"".join([chunk async for chunk in stream])  # type: ignore[union-attr]
    return request.read()


def _anthropic_settings() -> dict[str, Any]:
    """Live model-access settings owned by the worker (``demo_bridge.anthropic``): mode
    ``proxy`` (the local Node server holds the key) or ``byok`` (visitor's key, memory only)."""
    if demo_bridge is None or getattr(demo_bridge, "anthropic", None) is None:
        return {"mode": "proxy", "proxyUrl": "", "apiKey": "", "workspaceId": "", "sessionToken": ""}
    settings = demo_bridge.anthropic
    return {key: str(getattr(settings, key, "") or "") for key in ("mode", "proxyUrl", "apiKey", "workspaceId", "sessionToken")}


def _route_anthropic(request: httpx.Request) -> tuple[str, list[tuple[str, str]]]:
    """Rewrite an Anthropic API request for the current mode. The SDK was built with a
    placeholder key and the public base URL; the real credentials never enter Python in
    proxy mode, and in BYOK mode they stay in this worker."""
    settings = _anthropic_settings()
    url = str(request.url)
    headers: list[tuple[str, str]] = []
    drop = {"x-api-key", "authorization", "anthropic-workspace-id", DIRECT_BROWSER_ACCESS_HEADER, "x-demo-session"}
    for key, value in request.headers.raw:
        name = key.decode()
        if name.lower() in SHOP_HOST_HEADER_SKIP or name.lower() in drop:
            continue
        headers.append((name, value.decode()))
    if settings["mode"] == "byok":
        headers.append(("x-api-key", settings["apiKey"]))
        headers.append((DIRECT_BROWSER_ACCESS_HEADER, "true"))
        if settings["workspaceId"]:
            headers.append(("anthropic-workspace-id", settings["workspaceId"]))
        return url, headers
    proxy = settings["proxyUrl"].rstrip("/")
    if not proxy:
        raise httpx.ConnectError("no Anthropic proxy configured and no BYOK key set", request=request)
    path = request.url.raw_path.decode()
    if settings["sessionToken"]:
        headers.append(("x-demo-session", settings["sessionToken"]))
    return f"{proxy}{path}", headers


class BrowserTransport(httpx.AsyncBaseTransport):
    """Replaces ``httpx.AsyncHTTPTransport`` (constructor kwargs are accepted and ignored)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if _is_shop_url(request.url):
            return await self._via_shop_bridge(request)
        return await self._via_fetch(request)

    async def _via_shop_bridge(self, request: httpx.Request) -> httpx.Response:
        body = await _request_body(request)
        headers = {k.decode(): v.decode() for k, v in request.headers.raw if k.decode().lower() not in SHOP_HOST_HEADER_SKIP}
        try:
            result = await demo_bridge.shopRequest(
                request.method,
                str(request.url),
                json.dumps(headers),
                to_js(body) if body else None,
            )
        except JsException as error:
            raise httpx.ConnectError(f"shop bridge: {error}", request=request) from None
        status = int(result.status)
        raw_headers = json.loads(str(result.headersJson))
        header_list: list[tuple[str, str]] = []
        for key, value in raw_headers.items():
            if key.lower() in RESPONSE_HEADER_SKIP:
                continue
            for item in value if isinstance(value, list) else [value]:
                if item is not None and item != "":
                    header_list.append((key, str(item)))
        payload = bytes(result.body.to_py()) if result.body is not None else b""
        return httpx.Response(status, headers=header_list, stream=_BytesBody(payload), request=request)

    async def _via_fetch(self, request: httpx.Request) -> httpx.Response:
        body = await _request_body(request)
        if request.url.host == ANTHROPIC_HOST:
            url, header_pairs = _route_anthropic(request)
        else:
            url = str(request.url)
            header_pairs = [(k.decode(), v.decode()) for k, v in request.headers.raw if k.decode().lower() not in SHOP_HOST_HEADER_SKIP]
        js_headers = JsHeaders.new()
        for name, value in header_pairs:
            js_headers.append(name, value)
        init: dict[str, Any] = {"method": request.method, "headers": js_headers}
        if body:
            init["body"] = to_js(body)
        try:
            response = await js_fetch(url, to_js(init, dict_converter=JsObject.fromEntries))
        except JsException as error:
            raise httpx.ConnectError(str(error), request=request) from None
        header_list: list[tuple[str, str]] = []

        def collect(value: str, key: str, *_: Any) -> None:
            if key.lower() not in RESPONSE_HEADER_SKIP:
                header_list.append((key, value))

        response.headers.forEach(collect)
        return httpx.Response(
            int(response.status),
            headers=header_list,
            stream=_ReadableStreamBody(response.body),
            request=request,
        )


def install_transport() -> None:
    """Must run before ``anthropic`` (which binds ``httpx.AsyncHTTPTransport`` at import).
    Also installs the thread-free anyio shim the ASGI apps need."""
    httpx.AsyncHTTPTransport = BrowserTransport  # type: ignore[misc,assignment]
    import httpx._client as httpx_client

    httpx_client.AsyncHTTPTransport = BrowserTransport  # type: ignore[attr-defined]
    install_threadless_anyio()


def install_threadless_anyio() -> None:
    """Pyodide has no worker threads (``RuntimeError: can't start new thread``). Starlette and
    FastAPI push sync endpoints, sync dependencies and sync iterators through
    ``anyio.to_thread.run_sync``; run them inline on the event loop instead. Everything in this
    worker is single-threaded anyway, so nothing is lost."""
    import anyio.to_thread as anyio_to_thread

    async def run_sync_inline(func: Callable[..., Any], *args: Any, **_: Any) -> Any:
        return func(*args)

    anyio_to_thread.run_sync = run_sync_inline  # type: ignore[assignment]


# --------------------------------------------------------------------------------- ASGI


class HostApp:
    """Drives one ASGI application in-process: lifespan once, then request by request."""

    def __init__(self, app: Any, name: str) -> None:
        self.app = app
        self.name = name
        self._lifespan_task: asyncio.Task[None] | None = None
        self._lifespan_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._startup = asyncio.get_event_loop().create_future()
        self._shutdown = asyncio.get_event_loop().create_future()

    async def start(self) -> None:
        scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": {}}
        self._state = scope["state"]

        async def receive() -> dict[str, Any]:
            return await self._lifespan_queue.get()

        async def send(message: dict[str, Any]) -> None:
            kind = message["type"]
            if kind == "lifespan.startup.complete" and not self._startup.done():
                self._startup.set_result(None)
            elif kind == "lifespan.startup.failed" and not self._startup.done():
                self._startup.set_exception(RuntimeError(message.get("message", "lifespan startup failed")))
            elif kind in ("lifespan.shutdown.complete", "lifespan.shutdown.failed") and not self._shutdown.done():
                self._shutdown.set_result(None)

        async def run() -> None:
            try:
                await self.app(scope, receive, send)
            except Exception as error:  # the app does not support lifespan or crashed
                if not self._startup.done():
                    self._startup.set_exception(error)

        self._lifespan_task = asyncio.get_event_loop().create_task(run())
        await self._lifespan_queue.put({"type": "lifespan.startup"})
        await asyncio.wait_for(self._startup, LIFESPAN_TIMEOUT_S)
        logger.info("%s: lifespan startup complete", self.name)

    async def handle(
        self,
        method: str,
        target: str,
        headers_json: str,
        body: Any,
        send_head: Callable[[int, str], Any],
        send_chunk: Callable[[Any], Any],
    ) -> None:
        """One request. ``send_head(status, headers_json)`` once, ``send_chunk(bytes)`` per
        body part; the JS side closes its stream when this coroutine returns."""
        path, _, query = target.partition("?")
        raw_headers = json.loads(headers_json) if headers_json else {}
        header_items = [(k.lower().encode("latin-1"), str(v).encode("latin-1")) for k, v in raw_headers.items()]
        if not any(k == b"host" for k, _ in header_items):
            header_items.append((b"host", b"localhost"))
        # JS null crosses as a JsNull proxy (not Python None); only real byte buffers have to_py().
        payload = bytes(body.to_py()) if body is not None and hasattr(body, "to_py") else b""
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": query.encode("utf-8"),
            "root_path": "",
            "headers": header_items,
            "client": ASGI_CLIENT,
            "server": ASGI_SERVER,
            "state": dict(getattr(self, "_state", {})),
        }
        delivered = False
        disconnect = asyncio.get_event_loop().create_future()

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await disconnect
            return {"type": "http.disconnect"}

        started = False

        async def send(message: dict[str, Any]) -> None:
            nonlocal started
            kind = message["type"]
            if kind == "http.response.start":
                started = True
                headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in message.get("headers", [])]
                send_head(int(message["status"]), json.dumps(headers))
            elif kind == "http.response.body":
                chunk = message.get("body", b"")
                if chunk:
                    send_chunk(to_js(bytes(chunk)))

        try:
            await self.app(scope, receive, send)
        except Exception:
            logger.error("%s: unhandled error in %s %s\n%s", self.name, method, target, traceback.format_exc())
            if not started:
                send_head(500, json.dumps([("content-type", "application/json")]))
                send_chunk(to_js(json.dumps({"detail": "host error", "error": traceback.format_exc()[-2000:]}).encode()))
        finally:
            if not disconnect.done():
                disconnect.set_result(None)


# --------------------------------------------------------------------------------- boot

_apps: dict[str, HostApp] = {}


def configure_environment(env: dict[str, str]) -> None:
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def boot_role(role: str) -> HostApp:
    """Import the repo's app module for ``role`` and run its lifespan startup."""
    if role in _apps:
        return _apps[role]
    if role == "shopping":
        from storefront.api.main import app  # noqa: PLC0415
    elif role == "merchant":
        from merchant.api.main import app  # noqa: PLC0415
    else:
        raise ValueError(f"unknown role {role!r}")
    host = HostApp(app, role)
    await host.start()
    _apps[role] = host
    return host


async def handle_request(role: str, method: str, target: str, headers_json: str, body: Any, send_head: Any, send_chunk: Any) -> None:
    host = _apps.get(role)
    if host is None:
        send_head(503, json.dumps([("content-type", "application/json")]))
        send_chunk(to_js(json.dumps({"detail": f"{role} host not booted"}).encode()))
        return
    await host.handle(method, target, headers_json, body, send_head, send_chunk)


def versions() -> str:
    import anthropic  # noqa: PLC0415
    import pydantic  # noqa: PLC0415

    return json.dumps(
        {
            "python": sys.version.split()[0],
            "anthropic": anthropic.__version__,
            "httpx": httpx.__version__,
            "pydantic": pydantic.VERSION,
        }
    )
