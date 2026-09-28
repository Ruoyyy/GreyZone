# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Handoff code issuance/verification (ADR-10). The PHP verifier mirrors ``verify``;
``test_vector`` pins a code the plugin's PHPUnit test decodes too."""

from __future__ import annotations

import base64
import json

import pytest

from shopware_common.handoff import (
    MAX_TTL_SECONDS,
    HandoffCodeError,
    HandoffCodeIssuer,
    HandoffCodeVerifier,
    derive_keys,
    generate_secret,
)

SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
TOKEN = "SWSCVGF5ZUJQBWJ0QMP1OXPZNQ"  # 26 chars, sw-context-token alphabet
NOW = 1_800_000_000


def test_roundtrip_and_single_use():
    issuer = HandoffCodeIssuer(SECRET)
    verifier = HandoffCodeVerifier(SECRET)
    issued = issuer.issue(TOKEN, now=NOW)
    assert issued.expires_at - issued.issued_at == MAX_TTL_SECONDS
    assert "." in issued.code and TOKEN not in issued.code
    assert verifier.verify(issued.code, now=NOW + 5) == TOKEN
    with pytest.raises(HandoffCodeError, match="already used"):
        verifier.verify(issued.code, now=NOW + 6)


def test_expired_and_future_codes_are_refused():
    issuer = HandoffCodeIssuer(SECRET, ttl_seconds=60)
    verifier = HandoffCodeVerifier(SECRET)
    code = issuer.issue(TOKEN, now=NOW).code
    with pytest.raises(HandoffCodeError, match="expired"):
        verifier.verify(code, now=NOW + 61)
    with pytest.raises(HandoffCodeError, match="future"):
        verifier.verify(code, now=NOW - 120)


def test_tampering_is_detected():
    issuer = HandoffCodeIssuer(SECRET)
    verifier = HandoffCodeVerifier(SECRET)
    code = issuer.issue(TOKEN, now=NOW).code
    payload, mac = code.split(".")
    with pytest.raises(HandoffCodeError, match="signature"):
        verifier.verify(payload + "x." + mac, now=NOW)
    with pytest.raises(HandoffCodeError, match="signature"):
        verifier.verify(payload + "." + mac[:-2] + "AA", now=NOW)
    with pytest.raises(HandoffCodeError, match="signature"):
        HandoffCodeVerifier(generate_secret()).verify(code, now=NOW)
    with pytest.raises(HandoffCodeError):
        verifier.verify("not-a-code", now=NOW)


def test_ttl_and_secret_bounds():
    with pytest.raises(ValueError):
        HandoffCodeIssuer(SECRET, ttl_seconds=MAX_TTL_SECONDS + 1)
    with pytest.raises(ValueError):
        HandoffCodeIssuer("short")
    with pytest.raises(ValueError):
        HandoffCodeIssuer(SECRET).issue("bad token!")


def test_vector_matches_php_fixture():
    """Deterministic parts of the contract the PHP test asserts against the same secret:
    derived keys and the payload shape. The box itself is random per issue."""
    enc_key, mac_key = derive_keys(SECRET)
    assert enc_key.hex() == "fe15d237bdb5f946db071f5824cdb37cf7275762a5865ef3e689047d20c39b43"
    assert mac_key.hex() == "1c8b5115df163e3d02efda4cccd28e0033d7b3a23865f3f7de9e5e9c3e7f1912"
    issued = HandoffCodeIssuer(SECRET).issue(TOKEN, now=NOW)
    payload_b64 = issued.code.split(".")[0]
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    assert list(payload) == ["exp", "iat", "jti", "tok", "v"]
    assert payload["v"] == 1 and payload["iat"] == NOW and payload["exp"] == NOW + 120
