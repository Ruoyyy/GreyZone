# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from shopware_common.http_signing import (
    RequestSigner,
    content_digest,
    key_id_for,
    public_jwk,
    target_uri,
    verify,
)

BODY = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
URL = "http://localhost:8080/ucp/mcp"


def test_content_digest_is_rfc9530_sha256():
    expected = base64.b64encode(hashlib.sha256(BODY).digest()).decode()
    assert content_digest(BODY) == f"sha-256=:{expected}:"
    assert content_digest(b"") == "sha-256=:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=:"


def test_signature_input_matches_php_sdk_profile():
    signer = RequestSigner.generate(lifetime_seconds=120)
    headers = signer.sign("POST", URL, BODY, created=1_800_000_000)
    assert headers.signature_input == (
        'sig=("@method" "@target-uri" "content-digest");created=1800000000;'
        f'expires=1800000120;keyid="{signer.kid}";alg="ES256"'
    )
    assert headers.signature.startswith("sig=:") and headers.signature.endswith(":")
    # openssl_verify wants DER: a SEQUENCE, never the 64-byte raw r||s JOSE form.
    raw = base64.b64decode(headers.signature[len("sig=:") : -1])
    assert raw[0] == 0x30 and len(raw) != 64


def test_verify_roundtrip_and_rejects_body_changes():
    signer = RequestSigner.generate()
    headers = signer.headers_for("POST", URL, BODY)
    assert verify(signer.public_key, "POST", URL, BODY, headers) == signer.kid
    with pytest.raises(ValueError, match="Content-Digest"):
        verify(signer.public_key, "POST", URL, BODY + b" ", headers)
    with pytest.raises(ValueError, match="does not verify"):
        verify(signer.public_key, "POST", URL + "?x=1", BODY, headers)
    with pytest.raises(ValueError, match="does not verify"):
        verify(RequestSigner.generate().public_key, "POST", URL, BODY, headers)


def test_expiry_window():
    signer = RequestSigner.generate(lifetime_seconds=120)
    headers = signer.sign("GET", URL, b"", created=1_800_000_000).as_dict()
    verify(signer.public_key, "GET", URL, b"", headers, now=1_800_000_100)
    with pytest.raises(ValueError, match="expired"):
        verify(signer.public_key, "GET", URL, b"", headers, now=1_800_000_300)
    with pytest.raises(ValueError, match="future"):
        verify(signer.public_key, "GET", URL, b"", headers, now=1_799_999_000)


def test_jwk_and_kid_are_deterministic_for_a_key():
    signer = RequestSigner.generate()
    again = RequestSigner.from_pem(signer.private_pem())
    assert again.kid == signer.kid == key_id_for(signer.public_key)
    jwk = public_jwk(signer.public_key)
    assert jwk["kty"] == "EC" and jwk["crv"] == "P-256" and jwk["alg"] == "ES256"
    assert jwk["use"] == "sig" and len(base64.urlsafe_b64decode(jwk["x"] + "=")) == 32
    assert signer.jwk() == jwk


def test_only_p256_keys_are_accepted():
    with pytest.raises(ValueError, match="P-256"):
        RequestSigner(ec.generate_private_key(ec.SECP384R1()))
    with pytest.raises(ValueError):
        RequestSigner.generate(lifetime_seconds=301)


def test_target_uri_drops_fragment_keeps_port_and_query():
    assert target_uri("http://localhost:8080/ucp/v1/carts?x=1#frag") == (
        "http://localhost:8080/ucp/v1/carts?x=1"
    )
    assert target_uri("https://shop.example") == "https://shop.example/"
