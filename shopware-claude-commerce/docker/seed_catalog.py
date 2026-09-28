#!/usr/bin/env python3
"""Seed the demo shop for the agents (M5/M6) — idempotent, Admin API only.

    python docker/seed_catalog.py --shop-url http://localhost:8080 [--user admin --password shopware]

* Catalog: size variants (one out of stock) and a Grundpreis product, all with a delivery
  time ("2-4 Tage") so the storefront can show an ETA. Products are looked up by
  ``productNumber`` (CA-TSHIRT*, CA-OIL).
* Shipping: the Standard method costs 4.90 € gross, Express 9.90 € gross with a "1-2 Tage"
  delivery time; both are assigned to the Storefront sales channel. The host reads
  ``POST /store-api/shipping-method`` → ``prices[].currencyPrice[]`` and ``deliveryTime``.
* Policies (M6): CMS pages (type ``page``, one text block each) for Widerruf/Rückgabe,
  Versand & Lieferzeit, AGB and Datenschutz under the footer navigation, plus "Kontakt" in
  the service navigation. The sales channel's ``footerCategoryId`` / ``serviceCategoryId``
  are created and set when missing. Pages and categories are looked up by name.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from _bootstrap_lib import CURRENCY_EUR, AdminApi, AdminApiError, new_id

DELIVERY_TIME_STANDARD = {"name": "2-4 Tage", "min": 2, "max": 4, "unit": "day"}
DELIVERY_TIME_EXPRESS = {"name": "1-2 Tage", "min": 1, "max": 2, "unit": "day"}
SHIPPING_STANDARD_TECHNICAL_NAME = "shipping_standard"
SHIPPING_EXPRESS_TECHNICAL_NAME = "shipping_express"
SHIPPING_STANDARD_GROSS = 4.90
SHIPPING_EXPRESS_GROSS = 9.90
VAT_RATE_PERCENT = 19.0
SHIPPING_CALCULATION_LINE_ITEM_COUNT = 1
VISIBILITY_ALL = 30

SEEDED_PRODUCT_NUMBERS = ("CA-TSHIRT", "CA-TSHIRT-S", "CA-TSHIRT-M", "CA-TSHIRT-L", "CA-OIL")

FOOTER_ROOT_NAME = "Footer"
FOOTER_COLUMN_NAME = "Rechtliches & Service"
SERVICE_ROOT_NAME = "Service"

# (category/page name, CMS text). German demo copy; the storefront's policies.py indexes it.
POLICY_PAGES: tuple[tuple[str, str], ...] = (
    (
        "Widerrufsbelehrung / Rückgabe",
        "<h2>Widerrufsrecht</h2><p>Sie haben das Recht, binnen <strong>14 Tagen</strong> ohne Angabe von "
        "Gründen diesen Vertrag zu widerrufen. Die Widerrufsfrist beträgt vierzehn Tage ab dem Tag, an dem "
        "Sie oder ein von Ihnen benannter Dritter die Waren in Besitz genommen haben.</p>"
        "<h3>Rückgabe</h3><p>Senden Sie die Ware innerhalb von 14 Tagen nach dem Widerruf an uns zurück. "
        "Die Rücksendung ist für Sie kostenlos; ein Retourenlabel erhalten Sie per E-Mail. Die Rückerstattung "
        "erfolgt innerhalb von 14 Tagen nach Eingang der Ware über das ursprüngliche Zahlungsmittel.</p>",
    ),
    (
        "Versand & Lieferzeit",
        "<h2>Versand</h2><p>Standardversand innerhalb Deutschlands kostet <strong>4,90 €</strong>, "
        "Expressversand <strong>9,90 €</strong>. Ab einem Bestellwert von 50 € versenden wir kostenfrei.</p>"
        "<h3>Lieferzeit</h3><p>Standard: 2–4 Werktage. Express: 1–2 Werktage. Bestellungen, die bis 14 Uhr "
        "eingehen, verlassen unser Lager noch am selben Werktag. Sie erhalten eine Versandbestätigung mit "
        "Sendungsverfolgung per E-Mail.</p>",
    ),
    (
        "AGB",
        "<h2>Allgemeine Geschäftsbedingungen</h2><p><strong>§ 1 Geltungsbereich.</strong> Diese AGB gelten "
        "für alle Bestellungen über unseren Online-Shop, auch wenn sie über einen KI-Assistenten vorbereitet "
        "wurden. Der Vertrag kommt erst mit Ihrer Bestellung im Checkout zustande.</p>"
        "<p><strong>§ 2 Preise und Zahlung.</strong> Alle Preise sind Bruttopreise inklusive gesetzlicher "
        "Mehrwertsteuer. Wir akzeptieren Rechnung, Vorkasse und Nachnahme.</p>"
        "<p><strong>§ 3 Lieferung.</strong> Die Lieferung erfolgt an die im Checkout angegebene Adresse. "
        "Es gelten die auf der Seite Versand &amp; Lieferzeit genannten Fristen.</p>",
    ),
    (
        "Datenschutz",
        "<h2>Datenschutzerklärung</h2><p>Wir verarbeiten Ihre Daten ausschließlich zur Abwicklung Ihrer "
        "Bestellung (Art. 6 Abs. 1 lit. b DSGVO). Ein KI-Assistent erhält nur die Daten, die für Suche, "
        "Warenkorb und Bestellstatus notwendig sind; Zahlungsdaten werden nie an den Assistenten übergeben.</p>"
        "<p>Sie haben jederzeit das Recht auf Auskunft, Berichtigung und Löschung Ihrer Daten. Wenden Sie sich "
        "dazu an datenschutz@example.com.</p>",
    ),
)
CONTACT_PAGE: tuple[str, str] = (
    "Kontakt",
    "<h2>Kontakt</h2><p>Sie erreichen unseren Kundenservice Montag bis Freitag von 9 bis 17 Uhr unter "
    "<strong>+49 30 1234567</strong> oder per E-Mail an service@example.com. Für Fragen zu einer Bestellung "
    "halten Sie bitte Ihre Bestellnummer bereit.</p>",
)


def net_from_gross(gross: float, vat_rate: float = VAT_RATE_PERCENT) -> float:
    return round(gross / (1 + vat_rate / 100), 2)


# --------------------------------------------------------------------------- lookups


def tax_id(api: AdminApi) -> str:
    rows = api.search("tax", {"limit": 1, "sort": [{"field": "taxRate", "order": "DESC"}]})
    return str(rows[0]["id"])


def ensure_delivery_time(api: AdminApi, spec: dict[str, Any]) -> str:
    existing = api.search_one("delivery-time", "name", spec["name"])
    if existing:
        return str(existing["id"])
    delivery_time_id = new_id()
    api.request("POST", "/api/delivery-time", {"id": delivery_time_id, **spec})
    print(f"  delivery time created: {spec['name']}")
    return delivery_time_id


def ensure_property_group(api: AdminApi, name: str, options: list[str]) -> dict[str, str]:
    group = api.search_one("property-group", "name", name)
    if group is None:
        group_id = new_id()
        api.request(
            "POST",
            "/api/property-group",
            {
                "id": group_id,
                "name": name,
                "displayType": "text",
                "sortingType": "alphanumeric",
                "options": [{"id": new_id(), "name": option} for option in options],
            },
        )
    else:
        group_id = str(group["id"])
    rows = api.search(
        "property-group-option",
        {"limit": 50, "filter": [{"type": "equals", "field": "groupId", "value": group_id}]},
    )
    mapping = {str(row.get("name") or ""): str(row["id"]) for row in rows}
    for option in options:
        if option not in mapping:
            option_id = new_id()
            api.request(
                "POST",
                "/api/property-group-option",
                {"id": option_id, "groupId": group_id, "name": option},
            )
            mapping[option] = option_id
    return mapping


def ensure_unit(api: AdminApi, short_code: str = "l", name: str = "Liter") -> str | None:
    existing = api.search_one("unit", "shortCode", short_code)
    if existing:
        return str(existing["id"])
    unit_id = new_id()
    try:
        api.request("POST", "/api/unit", {"id": unit_id, "name": name, "shortCode": short_code})
    except AdminApiError:
        return None
    return unit_id


# --------------------------------------------------------------------------- products


def upsert_product(api: AdminApi, payload: dict[str, Any]) -> str:
    existing = api.search_one("product", "productNumber", payload["productNumber"])
    if existing:
        patch = {
            key: payload[key]
            for key in ("deliveryTimeId",)
            if key in payload and existing.get(key) != payload[key]
        }
        if patch:
            api.request("PATCH", f"/api/product/{existing['id']}", patch)
            print(f"  updated: {payload['productNumber']} ({', '.join(patch)})")
        else:
            print(f"  already present: {payload['productNumber']}")
        return str(existing["id"])
    api.request("POST", "/api/product", payload)
    print(f"  created: {payload['productNumber']}")
    return str(payload["id"])


def seed_products(api: AdminApi, channel: dict[str, Any]) -> None:
    tax = tax_id(api)
    channel_id = str(channel["id"])
    category = str(
        channel.get("navigationCategoryId") or api.search("category", {"limit": 1})[0]["id"]
    )
    sizes = ensure_property_group(api, "Size", ["S", "M", "L"])
    litre = ensure_unit(api)
    delivery_time = ensure_delivery_time(api, DELIVERY_TIME_STANDARD)

    visibilities = [{"salesChannelId": channel_id, "visibility": VISIBILITY_ALL}]
    categories = [{"id": category}]
    price = [{"currencyId": CURRENCY_EUR, "gross": 29.99, "net": 25.20, "linked": True}]

    parent_id = upsert_product(
        api,
        {
            "id": new_id(),
            "productNumber": "CA-TSHIRT",
            "name": "Claude Commerce T-Shirt",
            "description": "Organic cotton T-shirt in three sizes. Size L is currently sold out.",
            "stock": 0,
            "taxId": tax,
            "price": price,
            "active": True,
            "deliveryTimeId": delivery_time,
            "visibilities": visibilities,
            "categories": categories,
            "configuratorSettings": [{"optionId": sizes[size]} for size in ("S", "M", "L")],
        },
    )
    for size, stock, number in (
        ("S", 20, "CA-TSHIRT-S"),
        ("M", 12, "CA-TSHIRT-M"),
        ("L", 0, "CA-TSHIRT-L"),
    ):
        upsert_product(
            api,
            {
                "id": new_id(),
                "parentId": parent_id,
                "productNumber": number,
                "name": f"Claude Commerce T-Shirt — {size}",
                "stock": stock,
                "isCloseout": size == "L",
                "taxId": tax,
                "price": price,
                "active": True,
                "deliveryTimeId": delivery_time,
                "options": [{"id": sizes[size]}],
            },
        )

    oil: dict[str, Any] = {
        "id": new_id(),
        "productNumber": "CA-OIL",
        "name": "Extra Virgin Olive Oil 500 ml",
        "description": "Cold-pressed olive oil. Grundpreis is shown on the product (PAngV).",
        "stock": 40,
        "taxId": tax,
        "price": [{"currencyId": CURRENCY_EUR, "gross": 12.90, "net": 10.84, "linked": True}],
        "active": True,
        "deliveryTimeId": delivery_time,
        "visibilities": visibilities,
        "categories": categories,
        "purchaseUnit": 0.5,
        "referenceUnit": 1.0,
        "packUnit": "bottle",
    }
    if litre:
        oil["unitId"] = litre
    upsert_product(api, oil)


# --------------------------------------------------------------------------- shipping


def _currency_price(gross: float) -> list[dict[str, Any]]:
    return [
        {"currencyId": CURRENCY_EUR, "gross": gross, "net": net_from_gross(gross), "linked": False}
    ]


def ensure_shipping_price(api: AdminApi, method: dict[str, Any], gross: float) -> None:
    prices = method.get("prices") or []
    eur_rows = [
        row
        for row in prices
        if any(price.get("currencyId") == CURRENCY_EUR for price in row.get("currencyPrice") or [])
    ]
    if not eur_rows:
        api.request(
            "POST",
            "/api/shipping-method-price",
            {
                "id": new_id(),
                "shippingMethodId": method["id"],
                "calculation": SHIPPING_CALCULATION_LINE_ITEM_COUNT,
                "quantityStart": 0,
                "currencyPrice": _currency_price(gross),
            },
        )
        print(f"  shipping {method['name']}: price created ({gross:.2f} EUR gross)")
        return
    row = eur_rows[0]
    current = next(p for p in row["currencyPrice"] if p.get("currencyId") == CURRENCY_EUR)
    if abs(float(current.get("gross") or 0) - gross) < 0.005:
        print(f"  shipping {method['name']}: price unchanged ({gross:.2f} EUR gross)")
        return
    api.request(
        "PATCH",
        f"/api/shipping-method-price/{row['id']}",
        {"currencyPrice": _currency_price(gross)},
    )
    print(f"  shipping {method['name']}: price set to {gross:.2f} EUR gross")


def seed_shipping(api: AdminApi, channel: dict[str, Any]) -> None:
    methods = api.search("shipping-method", {"limit": 50, "associations": {"prices": {}}})
    by_technical_name = {str(m.get("technicalName")): m for m in methods}
    by_name = {str(m.get("name")): m for m in methods}
    standard = by_technical_name.get(SHIPPING_STANDARD_TECHNICAL_NAME) or by_name.get("Standard")
    express = by_technical_name.get(SHIPPING_EXPRESS_TECHNICAL_NAME) or by_name.get("Express")
    if standard is None:
        raise RuntimeError("Standard shipping method missing")

    express_delivery = ensure_delivery_time(api, DELIVERY_TIME_EXPRESS)
    if express is None:
        express_id = new_id()
        api.request(
            "POST",
            "/api/shipping-method",
            {
                "id": express_id,
                "name": "Express",
                "technicalName": SHIPPING_EXPRESS_TECHNICAL_NAME,
                "active": True,
                "deliveryTimeId": express_delivery,
                "availabilityRuleId": standard.get("availabilityRuleId"),
                "taxType": "auto",
            },
        )
        express = api.request("GET", f"/api/shipping-method/{express_id}", headers={}).get(
            "data"
        ) or {"id": express_id, "name": "Express"}
        express["prices"] = []
        print("  shipping Express: created")
    elif express.get("deliveryTimeId") != express_delivery or not express.get("active"):
        api.request(
            "PATCH",
            f"/api/shipping-method/{express['id']}",
            {"deliveryTimeId": express_delivery, "active": True},
        )
        print("  shipping Express: delivery time set to 1-2 Tage")
    if not standard.get("active"):
        api.request("PATCH", f"/api/shipping-method/{standard['id']}", {"active": True})

    ensure_shipping_price(api, standard, SHIPPING_STANDARD_GROSS)
    ensure_shipping_price(api, express, SHIPPING_EXPRESS_GROSS)

    assigned = {
        str(m["id"])
        for m in (
            api.request(
                "GET", f"/api/sales-channel/{channel['id']}?associations[shippingMethods][]="
            ).get("data")
            or {}
        ).get("shippingMethods")
        or []
    }
    wanted = {str(standard["id"]), str(express["id"])}
    if not wanted <= assigned:
        api.request(
            "PATCH",
            f"/api/sales-channel/{channel['id']}",
            {"shippingMethods": [{"id": method_id} for method_id in sorted(assigned | wanted)]},
        )
        print("  shipping: Standard + Express assigned to the Storefront channel")


# --------------------------------------------------------------------------- policies (CMS)


def ensure_cms_page(api: AdminApi, name: str, html: str) -> str:
    existing = api.search_one("cms-page", "name", name)
    if existing:
        return str(existing["id"])
    page_id, section_id, block_id, slot_id = new_id(), new_id(), new_id(), new_id()
    api.request(
        "POST",
        "/api/cms-page",
        {
            "id": page_id,
            "type": "page",
            "name": name,
            "sections": [
                {
                    "id": section_id,
                    "type": "default",
                    "position": 0,
                    "sizingMode": "boxed",
                    "blocks": [
                        {
                            "id": block_id,
                            "type": "text",
                            "position": 0,
                            "sectionPosition": "main",
                            "slots": [
                                {
                                    "id": slot_id,
                                    "type": "text",
                                    "slot": "content",
                                    "config": {
                                        "content": {"source": "static", "value": html},
                                        "verticalAlign": {"source": "static", "value": None},
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    print(f"  cms page created: {name}")
    return page_id


def ensure_category(
    api: AdminApi,
    name: str,
    *,
    parent_id: str | None,
    category_type: str = "page",
    cms_page_id: str | None = None,
) -> str:
    filters: list[dict[str, Any]] = [{"type": "equals", "field": "name", "value": name}]
    filters.append({"type": "equals", "field": "parentId", "value": parent_id})
    rows = api.search("category", {"limit": 1, "filter": filters})
    if rows:
        category = rows[0]
        patch: dict[str, Any] = {}
        if cms_page_id and category.get("cmsPageId") != cms_page_id:
            patch["cmsPageId"] = cms_page_id
        if not category.get("active"):
            patch["active"] = True
        if patch:
            api.request("PATCH", f"/api/category/{category['id']}", patch)
        return str(category["id"])
    category_id = new_id()
    payload: dict[str, Any] = {
        "id": category_id,
        "name": name,
        "type": category_type,
        "active": True,
        "displayNestedProducts": False,
        "productAssignmentType": "product",
    }
    if parent_id:
        payload["parentId"] = parent_id
    if cms_page_id:
        payload["cmsPageId"] = cms_page_id
    api.request("POST", "/api/category", payload)
    print(f"  category created: {name}")
    return category_id


def seed_policies(api: AdminApi, channel: dict[str, Any]) -> None:
    channel_id = str(channel["id"])
    footer_root = channel.get("footerCategoryId") or ensure_category(
        api, FOOTER_ROOT_NAME, parent_id=None, category_type="folder"
    )
    service_root = channel.get("serviceCategoryId") or ensure_category(
        api, SERVICE_ROOT_NAME, parent_id=None, category_type="folder"
    )
    patch: dict[str, Any] = {}
    if channel.get("footerCategoryId") != footer_root:
        patch["footerCategoryId"] = footer_root
    if channel.get("serviceCategoryId") != service_root:
        patch["serviceCategoryId"] = service_root
    if patch:
        api.request("PATCH", f"/api/sales-channel/{channel_id}", patch)
        print(f"  sales channel: {', '.join(patch)} set")

    column = ensure_category(
        api, FOOTER_COLUMN_NAME, parent_id=str(footer_root), category_type="folder"
    )
    for name, html in POLICY_PAGES:
        page_id = ensure_cms_page(api, name, html)
        ensure_category(api, name, parent_id=column, cms_page_id=page_id)
    contact_name, contact_html = CONTACT_PAGE
    contact_page = ensure_cms_page(api, contact_name, contact_html)
    ensure_category(api, contact_name, parent_id=str(service_root), cms_page_id=contact_page)


# --------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="shopware")
    args = parser.parse_args()

    api = AdminApi(args.shop_url.rstrip("/"))
    try:
        api.login(args.user, args.password)
        channel = api.storefront_sales_channel()
        print("Products")
        seed_products(api, channel)
        print("Shipping")
        seed_shipping(api, channel)
        print("Policies")
        seed_policies(api, channel)
    except (AdminApiError, RuntimeError) as error:
        print(f"Seed failed: {error}", file=sys.stderr)
        return 1
    print("Catalog seed done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
