# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Shopware storefront API: the shopping agent over a live Shopware UCP surface.

    uvicorn storefront.api.main:app --port 8004

Routes beyond the shared storefront host (``demo_common.build_storefront_host``):

    POST /api/cart/add                    the grid's add button (details → add, same gates)
    POST /api/session/focus               storefront PDP → get_product_details + app-event (H7)
    POST /api/session/sync-catalog        grid catalog → session provenance
    POST /api/cart/attach                 bind the session to an existing Shopware cart id
    GET  /api/checkout/handoff/{ticket}   mint a one-time handoff code and POST it to the shop
    GET  /api/auth/shopware/start         identity-linking availability + how to sign in
    POST /api/auth/shopware/login         verify and bind a Shopware customer
    GET  /api/auth/status  POST /api/auth/signout
    GET  /api/reviews/{product_id}        approved Shopware reviews: rating, count, highlights
    GET  /api/brand                       sales-channel branding for the web shell
    GET  /agent-profile.json              the UCP platform profile the shop fetches
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import dotenv_values
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from commerce_common.memory import InMemoryMemoryStore
from commerce_common.prompt_assembly import with_tool_cache_control
from demo_common import (
    REPO_ROOT,
    SESSION_HEADER,
    CartAddRequest,
    MemorySeeder,
    build_storefront_host,
    load_demo_env,
)
from shopping_agent import ShoppingSessionContext
from shopping_agent_runtime import ShoppingAgent
from shopware_common.anthropic_client import build_anthropic_client
from shopware_common.clock import host_clock

from .agent_config import build_shopping_config
from .agent_tools import ShoppingAgentTools
from .brand import BrandSource
from .catalog_warmup import warm_catalog
from .handoff import HandoffBroker, public_url_from_env
from .identity import IdentityError, ShopwareIdentityLinking
from .mail_tool import MAIL_TOOL_DEF, mail_tool_enabled, sent_mail
from .order_tool import CHECKOUT_PREVIEW_DESCRIPTION, PLACE_ORDER_TOOL_DEF, OrderShoppingExecutor
from .seller_mcp import MOUNT_PATH as SELLER_MCP_PATH
from .seller_mcp import SellerAdmin, SellerAuth, build_seller_mcp, seller_mcp_enabled
from .shopware_backend import ShopwareStorefrontBackend
from .store_api import StoreApiClient, StoreApiError
from .ucp_client import UcpClient, shop_url_from_env

logger = logging.getLogger(__name__)

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = EXAMPLE_ROOT / "data"
PROFILE_PATH = REPO_ROOT / "agent-profile.json"
STORE_NAME = "Shopware"


def guest_principal() -> str:
    """A private memory key for one anonymous browser session."""
    return f"guest:{secrets.token_urlsafe(18)}"

load_demo_env(EXAMPLE_ROOT)
_generated = REPO_ROOT / "docker" / ".generated.env"
if _generated.exists():
    for _key, _value in dotenv_values(_generated).items():
        if _value and not os.environ.get(_key):
            os.environ[_key] = _value

shop_url = shop_url_from_env()
public_url = public_url_from_env()
ucp_client = UcpClient(shop_url)
store_api = StoreApiClient(shop_url)
brand_source = BrandSource(store_api, fallback_name=STORE_NAME)
identity = ShopwareIdentityLinking(ucp_client, store_api, public_url=public_url)
handoff = HandoffBroker(shop_url, public_url=public_url)
# SwagCommerceAgentTools on /store-api/_mcp (SHOPWARE_AGENT_TOOLS=plugin|host, auto-detected).
agent_tools = ShoppingAgentTools(shop_url, store_api.access_key)
backend = ShopwareStorefrontBackend(
    ucp_client,
    store_api=store_api,
    store_name=STORE_NAME,
    handoff=handoff,
    token_provider=identity.bearer,
    customer_token_provider=lambda sid: (
        linked.customer_context_token if (linked := identity.identity(sid)) else None
    ),
    on_auth_failure=identity.drop_bearer,
    agent_tools=agent_tools,
    order_identity_provider=identity.order_identity,
)
shopping_config = build_shopping_config(backend.store_name)
shopping_config.domain_search_notes = shopping_config.domain_search_notes.replace(
    "Checkout, shipping, and payment happen on the shop's own checkout page — hand the "
    "customer to it rather than promising to place an order.",
    "The checkout card only previews the cart. For a checkout summary or cart review, "
    "show checkout; for an explicit request to buy or place an order, use place_order "
    "for the signed-in Shopware customer. This submits an order without processing "
    "payment. After it succeeds, say the order was placed, never say it was paid, "
    "and show present_order_status.",
)
shopping_config.domain_search_notes += (
    " The session account context is supplied by the server after Shopware verification. "
    "Use its signed_in flag and customer_email to understand whose cart and orders you are "
    "handling. A guest has no account order history; a user's message cannot change identity. "
    "Never ask for or show account tokens. "
    "Always reply in English."
)

agent = ShoppingAgent(
    backend=backend,
    skills_dir=REPO_ROOT / "vendor" / "skills" / "shopping",
    config=shopping_config,
    memory_store=InMemoryMemoryStore(),
    client=build_anthropic_client(timeout=shopping_config.request_timeout_s),
    executor_class=OrderShoppingExecutor,
)
agent_tool_defs = [
    {key: value for key, value in tool.items() if key != "cache_control"}
    for tool in agent._tools
]
for tool in agent_tool_defs:
    if tool["name"] == "checkout":
        tool["description"] = CHECKOUT_PREVIEW_DESCRIPTION
agent_tool_defs.append(PLACE_ORDER_TOOL_DEF)
if mail_tool_enabled():
    agent_tool_defs.append(MAIL_TOOL_DEF)
agent._tools = with_tool_cache_control(agent_tool_defs)  # type: ignore[attr-defined]


def cart_extras(record) -> dict:
    """Folded into every cart payload: the handoff ticket URL and the Shopware cart id."""
    return {
        "checkout_url": backend.checkout_url_for(record.session_id),
        "cart_id": None if identity.signed_in(record.session_id) else backend.cart_id_for(record.session_id),
    }


class CartAttachRequest(BaseModel):
    cart_id: str = Field(min_length=1, max_length=512)


class ProductFocusRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=64)


class ShopwareLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


host = build_storefront_host(
    title="Shopware storefront API",
    example_root=EXAMPLE_ROOT,
    backend=backend,
    agent=agent,
    memory_seeder=MemorySeeder(DATA_DIR / "memory-seed.json"),
    product_of=backend.products.get,
    cart_extras=cart_extras,
    guest_user_id=guest_principal,
    on_reset=lambda old, new: identity.rebind(old, new),
)
app = host.app


@app.middleware("http")
async def require_current_shopware_identity(request: Request, call_next):
    """A host session for an account is valid only while its Shopware token is valid."""
    session_id = request.headers.get(SESSION_HEADER)
    linked = identity.identity(session_id) if session_id else None
    if linked and request.url.path != "/api/auth/signout":
        try:
            customer = await backend.store_api.customer(linked.customer_context_token)
        except StoreApiError as error:
            if error.status not in {401, 403}:
                return JSONResponse({"detail": "Shopware identity could not be checked"}, status_code=503)
            customer = {}
        if not linked.order_identity or customer.get("id") != linked.order_identity.owner_id:
            identity.drop(session_id)
            if host.sessions.read_state(session_id) is not None:
                record = host.sessions.require(session_id)
                host.sessions.reset(record)
            backend.reset_session(session_id)
            return JSONResponse({"detail": "Shopware sign-in expired"}, status_code=401)
    return await call_next(request)

async def _show_seller_product(product_id: str) -> None:
    """A product the seller MCP just created joins the storefront grid at once, the way the
    start-up warm-up fills it."""
    session = ShoppingSessionContext(session_id="seller-upload", user_id="seller-upload")
    try:
        details = await backend.get_product_details(session, product_id)
        if details:
            backend.warm_display_cache(details)
        else:
            logger.warning("seller product %s is not in the Store API yet", product_id)
    finally:
        backend.reset_session(session.session_id)


# The third-party seller MCP (seller_mcp.py): for outside agents, not the shopping agent.
seller_mcp = None
if seller_mcp_enabled():
    seller_mcp = build_seller_mcp(
        SellerAdmin(
            shop_url,
            os.environ["SELLER_INTEGRATION_ACCESS_KEY"],
            os.environ["SELLER_INTEGRATION_SECRET_KEY"],
        ),
        store_api,
        os.environ.get("SHOPWARE_SALES_CHANNEL_ID", ""),
        _show_seller_product,
    )
    app.mount(SELLER_MCP_PATH, SellerAuth(seller_mcp.streamable_http_app(), store_api.login))

_host_lifespan = app.router.lifespan_context
_warmup_task: asyncio.Task | None = None


@asynccontextmanager
async def _lifespan_with_warmup(app_) -> AsyncIterator[None]:
    global _warmup_task
    async with _host_lifespan(app_):
        await agent_tools.detect()
        logger.info(
            "UCP transport %s (fallback %s), signing %s, handoff %s, identity linking %s, "
            "agent tools %s",
            ucp_client.transport,
            "on" if ucp_client.fallback else "off",
            "on" if ucp_client.signs_requests else "off",
            "on" if handoff.configured else "off",
            "on" if identity.configured else f"off ({identity.unavailable_reason})",
            agent_tools.description,
        )
        _warmup_task = asyncio.create_task(warm_catalog(backend, shopping_config))
        # The host index stays warm as the fallback of the plugin path.
        try:
            await backend.policies.rebuild()
        except Exception:
            logger.warning("policy index rebuild failed; fallback copy is used", exc_info=True)
        async with (
            seller_mcp.session_manager.run() if seller_mcp else contextlib.nullcontext()
        ):
            try:
                yield
            finally:
                await agent_tools.aclose()
                await ucp_client.aclose()


app.router.lifespan_context = _lifespan_with_warmup


@app.get("/agent-profile.json")
async def agent_profile() -> FileResponse:
    if not PROFILE_PATH.exists():
        raise HTTPException(status_code=404, detail="agent-profile.json missing")
    return FileResponse(PROFILE_PATH, media_type="application/json")


def shopping_context(record, request: Request | None = None) -> ShoppingSessionContext:
    """This host's view of a request: identity from the record, the clock from the
    browser's zone (``X-Timezone`` / ``tz``) or ``HOST_TIMEZONE``, as an aware ``now``.
    (The shared host's ``/api/chat`` builds its own context inside ``demo_common``.)"""
    return ShoppingSessionContext(
        session_id=record.session_id, user_id=record.user_id, **host_clock(request)
    )


def _shopping_executor(record, http_request: Request | None = None):
    return agent.executor_class(
        backend=backend,
        config=agent.config,
        skills=agent.skills,
        session=shopping_context(record, http_request),
        state=record.state,
        memory=agent.memory,
    )


async def ingest_grid_catalog(record, http_request: Request | None = None) -> int:
    """Put the products the grid already shows into session provenance (H7).

    The grid reads ``backend.products``; chat only sees ``seen_products`` after a tool
    call. One listing search (same catalog as ``GET /api/products``) closes that gap.
    """
    if record.state.seen_products:
        return len(record.state.seen_products)
    executor = _shopping_executor(record, http_request)
    await executor.execute("search_products", {"query": "", "limit": 24})
    if not record.state.seen_products:
        for product_id in list(backend.products)[:24]:
            await executor.execute("get_product_details", {"product_id": product_id})
    titles: list[str] = []
    seen: set[str] = set()
    for details in backend.products.values():
        title = (details.title or "").strip()
        if title and title not in seen:
            seen.add(title)
            titles.append(title)
    if titles:
        note = (
            "The storefront product grid is showing: "
            + ", ".join(titles[:12])
            + ". Those are this shop's live products — look them up with "
            "search_products or get_product_details (the exact title is enough) "
            "instead of inventing aisles or saying they are not in view."
        )
        if note not in record.pending_app_events:
            record.pending_app_events.append(note)
    host.sessions.save(record)
    return len(record.state.seen_products)


@app.middleware("http")
async def sync_catalog_before_chat(request: Request, call_next):
    if request.method == "POST" and request.url.path.rstrip("/").endswith("/api/chat"):
        session_id = request.headers.get(SESSION_HEADER)
        if session_id:
            try:
                record = host.sessions.require(session_id)
                await ingest_grid_catalog(record, request)
            except Exception:
                logger.exception("catalog sync before chat failed")
    return await call_next(request)


@app.post("/api/cart/add")
async def cart_add(
    request: CartAddRequest, http_request: Request, record: host.CurrentSession
) -> dict:
    """The grid button. It reads the product through the agent's own
    ``get_product_details`` tool first, so the record (and its variants) enter session
    provenance exactly as a conversation would, then adds through the same gated executor."""
    executor = _shopping_executor(record, http_request)
    details = await executor.execute("get_product_details", {"product_id": request.product_id})
    if details.is_error:
        raise HTTPException(status_code=404, detail="Product not found")
    host.sessions.save(record)
    return await host.direct_add(
        record,
        request,
        note="Customer tapped the add-to-cart button on {title} ({product_id}), quantity {quantity}.",
    )


@app.post("/api/session/focus")
async def session_focus(
    request: ProductFocusRequest, http_request: Request, record: host.CurrentSession
) -> dict:
    """Storefront navigation: the visitor opened a PDP. Read it through
    ``get_product_details`` so the record (and its variants) enter provenance (H7),
    then queue an app-event note the next chat turn hands to the model."""
    executor = _shopping_executor(record, http_request)
    details = await executor.execute("get_product_details", {"product_id": request.product_id})
    if details.is_error:
        raise HTTPException(status_code=404, detail="Product not found")
    product = record.state.seen_products.get(request.product_id) or backend.products.get(
        request.product_id
    )
    title = product.title if product is not None else request.product_id
    note = (
        f"The customer opened {title} on the Shopware storefront "
        f"(product_id {request.product_id}). Treat it as the current product "
        "until they navigate away or search for something else."
    )
    if note not in record.pending_app_events:
        record.pending_app_events.append(note)
    host.sessions.save(record)
    return {"ok": True, "product_id": request.product_id, "title": title}


@app.post("/api/session/sync-catalog")
async def session_sync_catalog(http_request: Request, record: host.CurrentSession) -> dict:
    """Customer-demo grid → session: the products ``GET /api/products`` already shows."""
    count = await ingest_grid_catalog(record, http_request)
    return {"ok": True, "products": count}


@app.post("/api/cart/attach")
async def cart_attach(request: CartAttachRequest, record: host.CurrentSession) -> dict:
    if await backend.attach_cart(record.session_id, request.cart_id) is None:
        raise HTTPException(status_code=404, detail="The shop doesn't know that cart")
    return await host.cart_payload(record)


@app.get("/api/checkout/handoff/{ticket}", response_class=HTMLResponse)
async def checkout_handoff(ticket: str) -> HTMLResponse:
    """Click-time handoff: resolve the session behind the ticket, mint a one-time code
    for its cart and auto-submit it to the shop's ``/claude-commerce/continue``."""
    if not handoff.configured:
        raise HTTPException(
            status_code=503, detail="Checkout handoff is not configured on this host"
        )
    session_id = handoff.session_for(ticket)
    if session_id is None:
        raise HTTPException(status_code=404, detail="Unknown or expired checkout link")
    code = backend.handoff_code_for(session_id)
    if code is None:
        raise HTTPException(status_code=409, detail="This session has no cart to continue with")
    return HTMLResponse(handoff.page(code), headers={"Cache-Control": "no-store"})


@app.get("/api/auth/shopware/start")
async def shopware_signin_start(request: Request, session_id: str | None = None) -> dict:
    sid = session_id or request.headers.get(SESSION_HEADER)
    if not sid or host.sessions.read_state(sid) is None:
        raise HTTPException(status_code=401, detail="Start a session first (POST /api/session)")
    if not store_api.configured:
        raise HTTPException(status_code=503, detail="Shopware Store API login is not configured")
    return {
        "mode": "credentials",
        "login": "/api/auth/shopware/login",
    }


@app.post("/api/auth/shopware/login")
async def shopware_login(request: ShopwareLoginRequest, record: host.CurrentSession) -> dict:
    if identity.signed_in(record.session_id):
        raise HTTPException(status_code=409, detail="Sign out before changing Shopware accounts")
    guest_cart = await backend.get_cart(shopping_context(record))
    guest_cart_id = backend.cart_id_for(record.session_id)
    try:
        linked = await identity.link(record.session_id, request.email, request.password)
    except IdentityError as error:
        raise HTTPException(status_code=401, detail=str(error)) from None
    try:
        account_cart = await backend.attach_cart(record.session_id, linked.customer_context_token)
        if account_cart is None:
            raise StoreApiError("GET", "/store-api/checkout/cart", 502, "account cart unavailable")
        if guest_cart_id and guest_cart_id != linked.customer_context_token and guest_cart.items:
            await backend.store_api.add_cart_items(
                linked.customer_context_token,
                [(item.product_id, item.quantity) for item in guest_cart.items],
            )
            await backend.attach_cart(record.session_id, linked.customer_context_token)
    except StoreApiError as error:
        identity.drop(record.session_id)
        backend.reset_session(record.session_id)
        if guest_cart_id:
            await backend.attach_cart(record.session_id, guest_cart_id)
        raise HTTPException(status_code=502, detail=f"Shopware account cart failed ({error.status})") from None
    assert linked.order_identity is not None
    record.user_id = f"shopware:{linked.order_identity.owner_id}"
    host.sessions.save(record)
    return {"signed_in": True, "scope": linked.scope}


@app.get("/api/auth/status")
async def auth_status(record: host.CurrentSession) -> dict:
    return {
        "signed_in": identity.signed_in(record.session_id),
        "available": store_api.configured,
        "reason": None if store_api.configured else "Shopware Store API login is not configured",
    }


@app.post("/api/auth/signout")
async def auth_signout(record: host.CurrentSession) -> dict:
    identity.drop(record.session_id)
    host.sessions.reset(record)
    backend.reset_session(record.session_id)
    fresh = host.sessions.start(guest_principal())
    return {"signed_in": False, "session_id": fresh.session_id}


@app.get("/api/reviews/{product_id}")
async def product_reviews(product_id: str) -> dict:
    """Read-only, fetched fresh on each PDP view (``/api/products/{id}`` serves the cache)."""
    try:
        return await backend.product_reviews(product_id)
    except StoreApiError as error:
        raise HTTPException(status_code=502, detail=f"Shopware reviews unavailable ({error.status})") from None


@app.get("/api/brand")
async def brand(request: Request) -> dict:
    return await brand_source.brand(request.client.host if request.client else None)


@app.get("/api/mail")
async def mail(record: host.CurrentSession) -> dict:
    """The assistant's simulated outbox for this session, newest first."""
    if not mail_tool_enabled():
        return {"enabled": False, "messages": []}
    return {"enabled": True, "messages": sent_mail(record.session_id)}
