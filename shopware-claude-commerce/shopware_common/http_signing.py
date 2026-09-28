# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""RFC 9421 HTTP Message Signatures + RFC 9530 Content-Digest for UCP requests.

Profile as verified by ``ucp-php-sdk`` (``Rfc9421RequestSignatureService``):

* covered components ``("@method" "@target-uri" "content-digest")`` — fixed set, also
  for bodiless requests (digest of the empty body);
* parameters ``created``, ``expires`` (≤ 300 s apart), ``keyid``, ``alg="ES256"``;
* label ``sig``; ``Signature: sig=:<base64 DER ECDSA>:`` — the verifier calls
  ``openssl_verify``, which expects a DER-encoded ECDSA signature, not the raw ``r||s``
  form JOSE uses;
* ``Content-Digest: sha-256=:<base64 sha256(body)>:``.

The public key is published in the agent profile (``signing_keys``) as a JWK
(``kty=EC, crv=P-256, alg=ES256, use=sig, x, y, kid``). ``kid`` is derived from the
public key (base64url of SHA-256 over the uncompressed point) so re-running bootstrap on
the same PEM yields the same key id and the profile carries exactly one active key.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

SIGNATURE_LABEL = "sig"
ALGORITHM = "ES256"
COVERED_COMPONENTS = ('"@method"', '"@target-uri"', '"content-digest"')
DEFAULT_LIFETIME_SECONDS = 120
MAX_LIFETIME_SECONDS = 300
_P256_COORD_BYTES = 32
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def content_digest(body: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode("ascii") + ":"


def target_uri(url: str) -> str:
    """The absolute URI as the server reconstructs it: no fragment, default ports kept as
    given (Shopware sees ``http://localhost:8080`` through the published port)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def key_id_for(public_key: ec.EllipticCurvePublicKey) -> str:
    point = public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url(hashlib.sha256(point).digest())[:32]


def public_jwk(public_key: ec.EllipticCurvePublicKey, kid: str | None = None) -> dict[str, str]:
    numbers = public_key.public_numbers()
    return {
        "kid": kid or key_id_for(public_key),
        "kty": "EC",
        "crv": "P-256",
        "alg": ALGORITHM,
        "use": "sig",
        "x": _b64url(numbers.x.to_bytes(_P256_COORD_BYTES, "big")),
        "y": _b64url(numbers.y.to_bytes(_P256_COORD_BYTES, "big")),
    }


@dataclass(frozen=True)
class SignedHeaders:
    content_digest: str
    signature_input: str
    signature: str

    def as_dict(self) -> dict[str, str]:
        return {
            "Content-Digest": self.content_digest,
            "Signature-Input": self.signature_input,
            "Signature": self.signature,
        }


class RequestSigner:
    """ES256 signer over a P-256 private key (PEM)."""

    def __init__(
        self,
        private_key: ec.EllipticCurvePrivateKey,
        *,
        kid: str | None = None,
        lifetime_seconds: int = DEFAULT_LIFETIME_SECONDS,
    ) -> None:
        if not isinstance(private_key.curve, ec.SECP256R1):
            raise ValueError("UCP request signing needs a P-256 (secp256r1) key for ES256")
        if not 0 < lifetime_seconds <= MAX_LIFETIME_SECONDS:
            raise ValueError(f"signature lifetime must be within 1..{MAX_LIFETIME_SECONDS} s")
        self._key = private_key
        self.public_key = private_key.public_key()
        self.kid = kid or key_id_for(self.public_key)
        self.lifetime_seconds = lifetime_seconds

    @classmethod
    def from_pem(cls, pem: bytes | str, **kwargs: object) -> RequestSigner:
        raw = pem.encode("utf-8") if isinstance(pem, str) else pem
        key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("signing key PEM is not an EC private key")
        return cls(key, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def from_pem_file(cls, path: str | Path, **kwargs: object) -> RequestSigner:
        return cls.from_pem(Path(path).read_bytes(), **kwargs)

    @classmethod
    def generate(cls, **kwargs: object) -> RequestSigner:
        return cls(ec.generate_private_key(ec.SECP256R1()), **kwargs)  # type: ignore[arg-type]

    def private_pem(self) -> bytes:
        return self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def jwk(self) -> dict[str, str]:
        return public_jwk(self.public_key, self.kid)

    def signature_base(self, method: str, url: str, digest: str, created: int, expires: int) -> str:
        params = self.signature_params(created, expires)
        return "\n".join(
            [
                f'"@method": {method.upper()}',
                f'"@target-uri": {target_uri(url)}',
                f'"content-digest": {digest}',
                f'"@signature-params": {params}',
            ]
        )

    def signature_params(self, created: int, expires: int) -> str:
        components = " ".join(COVERED_COMPONENTS)
        return (
            f"({components});created={created};expires={expires};"
            f'keyid="{self.kid}";alg="{ALGORITHM}"'
        )

    def sign(
        self, method: str, url: str, body: bytes = b"", *, created: int | None = None
    ) -> SignedHeaders:
        created = int(created if created is not None else time.time())
        expires = created + self.lifetime_seconds
        digest = content_digest(body)
        base = self.signature_base(method, url, digest, created, expires)
        der_signature = self._key.sign(base.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return SignedHeaders(
            content_digest=digest,
            signature_input=f"{SIGNATURE_LABEL}={self.signature_params(created, expires)}",
            signature=f"{SIGNATURE_LABEL}=:{base64.b64encode(der_signature).decode('ascii')}:",
        )

    def headers_for(self, method: str, url: str, body: bytes = b"") -> dict[str, str]:
        return self.sign(method, url, body).as_dict()


def verify(
    public_key: ec.EllipticCurvePublicKey,
    method: str,
    url: str,
    body: bytes,
    headers: dict[str, str],
    *,
    now: int | None = None,
) -> str:
    """Verify a signed request the way the PHP SDK does. Returns the key id. Raises
    ``ValueError`` with the reason on failure. Used by tests and by bootstrap self-checks."""
    lowered = {k.lower(): v for k, v in headers.items()}
    signature_input = lowered.get("signature-input")
    signature = lowered.get("signature")
    digest = lowered.get("content-digest")
    if not signature_input or not signature or not digest:
        raise ValueError("missing signature headers")
    if digest != content_digest(body):
        raise ValueError("Content-Digest does not match the body")
    label, _, params = signature_input.partition("=")
    if label != SIGNATURE_LABEL:
        raise ValueError(f"unexpected signature label {label!r}")
    fields: dict[str, str] = {}
    for part in params.split(";")[1:]:
        name, _, value = part.partition("=")
        fields[name] = value.strip('"')
    try:
        created, expires = int(fields["created"]), int(fields["expires"])
    except (KeyError, ValueError) as error:
        raise ValueError("Signature-Input is missing created/expires") from error
    kid = fields.get("keyid")
    if not kid or fields.get("alg") != ALGORITHM:
        raise ValueError("Signature-Input needs keyid and alg=ES256")
    current = int(now if now is not None else time.time())
    if created > current + 60:
        raise ValueError("signature created in the future")
    if expires < current - 60:
        raise ValueError("signature expired")
    if expires - created > MAX_LIFETIME_SECONDS:
        raise ValueError("signature lifetime too long")
    prefix = f"{SIGNATURE_LABEL}=:"
    if not signature.startswith(prefix) or not signature.endswith(":"):
        raise ValueError("Signature header is malformed")
    raw_signature = base64.b64decode(signature[len(prefix) : -1])
    base = "\n".join(
        [
            f'"@method": {method.upper()}',
            f'"@target-uri": {target_uri(url)}',
            f'"content-digest": {digest}',
            f'"@signature-params": {params}',
        ]
    )
    try:
        public_key.verify(raw_signature, base.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as error:
        raise ValueError("signature does not verify") from error
    return kid


def signer_from_env(
    *, pem_file_var: str = "UCP_AGENT_SIGNING_KEY_PEM_FILE"
) -> RequestSigner | None:
    """The signer configured for this host, or ``None`` when no key file is set (the
    shop's ``signature-policy=log`` accepts unsigned requests locally)."""
    path = (os.environ.get(pem_file_var) or "").strip()
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        # Relative paths are repo-root relative (docker/.generated.env writes them that
        # way); the current directory is tried first for ad-hoc runs.
        cwd_candidate = Path.cwd() / candidate
        candidate = cwd_candidate if cwd_candidate.exists() else _REPO_ROOT / candidate
    if not candidate.exists():
        raise FileNotFoundError(f"{pem_file_var}={path} does not exist")
    return RequestSigner.from_pem_file(candidate)
