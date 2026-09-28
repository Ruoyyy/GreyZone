# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Handoff code contract (ADR-10) — the Python issuer and its reference verifier.

The shopping agent hands a UCP cart (= Shopware Store API context token) to the Twig
checkout. The token must never travel in a URL, so the host issues a **one-time,
short-lived, HMAC-signed handoff code** that encrypts the token, and the
``CommerceAgentsHandoff`` plugin (``docker/plugins/CommerceAgentsHandoff``) verifies it.
``verify`` below is the reference the PHP verifier mirrors; the tests pin both.

Wire format (``v=1``)::

    code       = b64url(payload) "." b64url(HMAC-SHA256(mac_key, payload))
    payload    = compact JSON, keys sorted:
                 {"exp": <unix>, "iat": <unix>, "jti": <32 hex>, "tok": <b64url(box)>, "v": 1}
    box        = nonce(12) || AES-256-GCM(enc_key, nonce, aad=jti, plaintext=token) || tag(16)
    enc_key    = HMAC-SHA256(secret, "commerce-agents-handoff:enc")
    mac_key    = HMAC-SHA256(secret, "commerce-agents-handoff:mac")

Rules both sides enforce: ``exp - iat <= 120`` s, ``exp >= now``, ``iat <= now + 60``
(clock skew), ``jti`` accepted once (the plugin stores consumed ids until ``exp``), the
decrypted token matches ``^[A-Za-z0-9_-]{16,128}$``. The secret is
``COMMERCE_AGENTS_HANDOFF_SECRET`` (≥ 32 bytes, generated once by bootstrap and shared
with the container's ``.env``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1
MAX_TTL_SECONDS = 120
DEFAULT_TTL_SECONDS = 120
CLOCK_SKEW_SECONDS = 60
MIN_SECRET_BYTES = 32
SECRET_ENV = "COMMERCE_AGENTS_HANDOFF_SECRET"
_ENC_INFO = b"commerce-agents-handoff:enc"
_MAC_INFO = b"commerce-agents-handoff:mac"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_JTI_BYTES = 16
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class HandoffCodeError(ValueError):
    """The code is malformed, forged, expired, or its token is not a context token."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (ValueError, TypeError) as error:
        raise HandoffCodeError("code is not base64url") from error


def derive_keys(secret: str | bytes) -> tuple[bytes, bytes]:
    raw = secret.encode("utf-8") if isinstance(secret, str) else secret
    if len(raw) < MIN_SECRET_BYTES:
        raise ValueError(f"{SECRET_ENV} must be at least {MIN_SECRET_BYTES} bytes")
    enc_key = hmac.new(raw, _ENC_INFO, hashlib.sha256).digest()
    mac_key = hmac.new(raw, _MAC_INFO, hashlib.sha256).digest()
    return enc_key, mac_key


def generate_secret() -> str:
    return secrets.token_hex(MIN_SECRET_BYTES)


def secret_from_env() -> str | None:
    value = (os.environ.get(SECRET_ENV) or "").strip()
    return value or None


@dataclass(frozen=True)
class HandoffCode:
    code: str
    jti: str
    issued_at: int
    expires_at: int


class HandoffCodeIssuer:
    def __init__(self, secret: str | bytes, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        if not 0 < ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError(f"handoff ttl must be within 1..{MAX_TTL_SECONDS} s")
        self._enc_key, self._mac_key = derive_keys(secret)
        self.ttl_seconds = ttl_seconds

    def issue(self, context_token: str, *, now: int | None = None) -> HandoffCode:
        if not TOKEN_PATTERN.match(context_token):
            raise ValueError("context token is not a Shopware sw-context-token")
        issued_at = int(now if now is not None else time.time())
        expires_at = issued_at + self.ttl_seconds
        jti = secrets.token_hex(_JTI_BYTES)
        nonce = secrets.token_bytes(_NONCE_BYTES)
        box = nonce + AESGCM(self._enc_key).encrypt(
            nonce, context_token.encode("utf-8"), jti.encode("ascii")
        )
        payload = _encode_payload(
            {"exp": expires_at, "iat": issued_at, "jti": jti, "tok": _b64url(box), "v": VERSION}
        )
        mac = hmac.new(self._mac_key, payload, hashlib.sha256).digest()
        return HandoffCode(
            code=f"{_b64url(payload)}.{_b64url(mac)}",
            jti=jti,
            issued_at=issued_at,
            expires_at=expires_at,
        )


class HandoffCodeVerifier:
    """Reference verifier (mirrors the PHP ``HandoffCodeVerifier``). ``consumed`` is the
    single-use store; the plugin uses a database table with the same semantics."""

    def __init__(self, secret: str | bytes) -> None:
        self._enc_key, self._mac_key = derive_keys(secret)
        self._consumed: dict[str, int] = {}

    def verify(self, code: str, *, now: int | None = None) -> str:
        current = int(now if now is not None else time.time())
        payload_b64, sep, mac_b64 = code.partition(".")
        if not sep or not payload_b64 or not mac_b64:
            raise HandoffCodeError("code must be <payload>.<mac>")
        payload = _unb64url(payload_b64)
        expected = hmac.new(self._mac_key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64url(mac_b64)):
            raise HandoffCodeError("signature mismatch")
        try:
            fields = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise HandoffCodeError("payload is not JSON") from error
        if not isinstance(fields, dict) or fields.get("v") != VERSION:
            raise HandoffCodeError("unsupported code version")
        try:
            issued_at, expires_at = int(fields["iat"]), int(fields["exp"])
            jti, box_b64 = str(fields["jti"]), str(fields["tok"])
        except (KeyError, TypeError, ValueError) as error:
            raise HandoffCodeError("payload is missing fields") from error
        if expires_at - issued_at > MAX_TTL_SECONDS or expires_at <= issued_at:
            raise HandoffCodeError("lifetime out of range")
        if expires_at < current:
            raise HandoffCodeError("code expired")
        if issued_at > current + CLOCK_SKEW_SECONDS:
            raise HandoffCodeError("code issued in the future")
        if not re.fullmatch(r"[0-9a-f]{32}", jti):
            raise HandoffCodeError("jti malformed")
        self._purge(current)
        if jti in self._consumed:
            raise HandoffCodeError("code already used")
        box = _unb64url(box_b64)
        if len(box) < _NONCE_BYTES + _TAG_BYTES + 1:
            raise HandoffCodeError("token box too short")
        try:
            token = AESGCM(self._enc_key).decrypt(
                box[:_NONCE_BYTES], box[_NONCE_BYTES:], jti.encode("ascii")
            )
        except InvalidTag as error:
            raise HandoffCodeError("token box does not authenticate") from error
        context_token = token.decode("utf-8", errors="replace")
        if not TOKEN_PATTERN.match(context_token):
            raise HandoffCodeError("decrypted value is not a context token")
        self._consumed[jti] = expires_at
        return context_token

    def _purge(self, now: int) -> None:
        for jti, expires_at in list(self._consumed.items()):
            if expires_at < now:
                del self._consumed[jti]


def _encode_payload(fields: dict[str, object]) -> bytes:
    return json.dumps(fields, separators=(",", ":"), sort_keys=True).encode("utf-8")
