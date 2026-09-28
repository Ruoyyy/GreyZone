"""Create the two local buyer accounts used to test storefront identity isolation.

Run from the repository root with ``.venv/bin/python docker/seed_buyers.py``.
This intentionally accepts only a loopback Shopware URL and does not create orders.
"""

from __future__ import annotations

import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BUYERS = (
    ("admin@shop.com", "admin", "Admin", "Buyer"),
    ("ruoyu@shop.com", "ruoyu", "Ruoyu", "Buyer"),
)
CONTAINER = "commerce-agents-shopware"


def set_local_password(email: str, password: str) -> None:
    """Only the requested local fixtures bypass Shopware's registration length rule."""
    hashed = subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "php",
            "-r",
            "echo password_hash($argv[1], PASSWORD_BCRYPT);",
            password,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if not hashed.startswith("$2y$"):
        raise RuntimeError("Shopware-compatible bcrypt hash was not produced")
    query = (
        "UPDATE customer SET password = '" + hashed + "' "
        "WHERE email = '" + email + "' AND guest = 0"
    )
    subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "mysql",
            "-h",
            "127.0.0.1",
            "-u",
            "root",
            "-proot",
            "shopware",
            "-e",
            query,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> int:
    settings = dotenv_values(ROOT / "docker" / ".generated.env")
    shop_url = str(settings.get("SHOPWARE_URL") or "").rstrip("/")
    access_key = str(settings.get("SHOPWARE_SALES_CHANNEL_ACCESS_KEY") or "")
    if urlparse(shop_url).hostname not in {"localhost", "127.0.0.1"} or not access_key:
        raise RuntimeError("A configured loopback Shopware Store API is required")

    headers = {"sw-access-key": access_key}
    with httpx.Client(base_url=shop_url, headers=headers, timeout=10) as client:
        salutations = client.post("/store-api/salutation", json={"limit": 1})
        salutations.raise_for_status()
        salutation_id = salutations.json()["elements"][0]["id"]
        countries = client.post(
            "/store-api/country",
            json={"filter": [{"type": "equals", "field": "iso", "value": "DE"}], "limit": 1},
        )
        countries.raise_for_status()
        country_id = countries.json()["elements"][0]["id"]

        for email, password, first_name, last_name in BUYERS:
            created = False
            logged_in = client.post(
                "/store-api/account/login", json={"username": email, "password": password}
            )
            if logged_in.status_code not in {200, 204}:
                temporary_password = secrets.token_urlsafe(24)
                address = {
                    "salutationId": salutation_id,
                    "firstName": first_name,
                    "lastName": last_name,
                    "street": "Demo Street 1",
                    "zipcode": "10115",
                    "city": "Berlin",
                    "countryId": country_id,
                }
                registered = client.post(
                    "/store-api/account/register",
                    json={
                        "guest": False,
                        "salutationId": salutation_id,
                        "firstName": first_name,
                        "lastName": last_name,
                        "email": email,
                        "password": temporary_password,
                        "storefrontUrl": shop_url,
                        "acceptedDataProtection": True,
                        "billingAddress": address,
                    },
                )
                if registered.is_error:
                    errors = registered.json().get("errors", [])
                    details = [(item.get("code"), item.get("detail")) for item in errors]
                    raise RuntimeError(
                        f"{email}: registration failed ({registered.status_code}): {details}"
                    )
                registered.raise_for_status()
                created = True
                set_local_password(email, password)
                logged_in = client.post(
                    "/store-api/account/login", json={"username": email, "password": password}
                )
            logged_in.raise_for_status()
            context_token = logged_in.headers.get("sw-context-token")
            if not context_token:
                raise RuntimeError(f"{email}: login did not return sw-context-token")
            customer = client.post(
                "/store-api/account/customer", headers={"sw-context-token": context_token}, json={}
            )
            customer.raise_for_status()
            if customer.json().get("email") != email:
                raise RuntimeError(f"{email}: logged-in customer does not match")
            print(f"{email}: {'created' if created else 'already present'}, login verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
