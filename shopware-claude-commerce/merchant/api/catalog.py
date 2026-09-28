# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Admin product rows → merchant ``Listing`` types, with the price/tax/cost facts the
staging paths need (a child inherits its family's price when its own ``price`` is null)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from merchant_agent import Listing, ListingDetails

from .admin_client import EUR_CURRENCY_ID, AdminTransport
from .insights import not_third_party

CATALOG_PAGE_SIZE = 100
DEFAULT_TAX_RATE = 19.0
PRODUCT_FIELDS = [
    "id",
    "name",
    "productNumber",
    "stock",
    "availableStock",
    "active",
    "price",
    "purchasePrices",
    "parentId",
    "childCount",
    "description",
    "metaTitle",
    "metaDescription",
    "taxId",
    "categoryIds",
    "translated",
    "tax",
    "categories",
]


def _translated(attrs: dict[str, Any], key: str) -> Any:
    value = attrs.get(key)
    if value in (None, ""):
        value = (attrs.get("translated") or {}).get(key)
    return value


def eur_entry(entries: Any) -> dict[str, Any] | None:
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("currencyId") == EUR_CURRENCY_ID:
            return entry
    return entries[0] if entries and isinstance(entries[0], dict) else None


def _gross(entries: Any) -> float | None:
    entry = eur_entry(entries)
    if entry is None:
        return None
    try:
        return float(entry.get("gross"))
    except (TypeError, ValueError):
        return None


def _status(active: bool, stock: int) -> str:
    if not active:
        return "paused"
    if stock <= 0:
        return "out_of_stock"
    return "active"


def _content_quality(description: str | None) -> str:
    text = (description or "").strip()
    if len(text) >= 120:
        return "good"
    if len(text) >= 40:
        return "needs_work"
    return "poor"


@dataclass
class ProductRecord:
    listing_id: str
    title: str
    product_number: str
    own_price: float | None
    stock: int
    active: bool
    parent_id: str | None
    description: str | None
    meta_title: str | None = None
    meta_description: str | None = None
    tax_id: str | None = None
    tax_rate: float | None = None
    price_entries: list[dict[str, Any]] | None = None
    purchase_price: float | None = None
    category: str | None = None
    category_ids: list[str] = field(default_factory=list)
    currency: str = "EUR"
    children: list[ProductRecord] = field(default_factory=list)
    parent: ProductRecord | None = field(default=None, repr=False)

    @property
    def inherits_price(self) -> bool:
        return self.own_price is None and self.parent is not None

    @property
    def price(self) -> float:
        """The price the storefront shows: the row's own or, for an inheriting child, the
        family's."""
        if self.own_price is not None:
            return self.own_price
        if self.parent is not None and self.parent.own_price is not None:
            return self.parent.own_price
        return 0.0

    @property
    def effective_tax_rate(self) -> float | None:
        if self.tax_rate is not None:
            return self.tax_rate
        return self.parent.tax_rate if self.parent is not None else None

    @property
    def is_family(self) -> bool:
        return bool(self.children)

    @property
    def status(self) -> str:
        if self.is_family:
            stock = sum(c.stock for c in self.children)
            active = self.active and any(c.active for c in self.children)
            return _status(active, stock)
        return _status(self.active, self.stock)

    def to_listing(self) -> Listing:
        options: dict[str, list[str]] = {}
        if self.children:
            options["Variant"] = [c.product_number for c in self.children]
        return Listing(
            listing_id=self.listing_id,
            title=self.title,
            status=self.status,  # type: ignore[arg-type]
            price=min((c.price for c in self.children), default=self.price)
            if self.children
            else self.price,
            currency=self.currency,
            stock=sum(c.stock for c in self.children) if self.children else self.stock,
            category=self.category,
            content_quality=_content_quality(self.description),  # type: ignore[arg-type]
            short_description=(self.description or "")[:160] or None,
            options=options,
            attributes={"productNumber": self.product_number} if self.product_number else {},
        )

    def to_variant_listing(self) -> Listing:
        return Listing(
            listing_id=self.listing_id,
            title=self.title,
            status=self.status,  # type: ignore[arg-type]
            price=self.price,
            currency=self.currency,
            stock=self.stock,
            variant_of=self.parent_id,
            option_values={"sku": self.product_number},
            category=self.category or (self.parent.category if self.parent else None),
        )

    def to_details(self) -> ListingDetails:
        listing = self.to_variant_listing() if self.parent_id else self.to_listing()
        return ListingDetails(
            **listing.model_dump(),
            long_description=self.description,
            missing_attributes=[] if self.description else ["description"],
            variants=[child.to_variant_listing() for child in self.children],
        )


class CatalogCache:
    def __init__(self, admin: AdminTransport) -> None:
        self._admin = admin
        self._by_id: dict[str, ProductRecord] = {}
        self._by_number: dict[str, ProductRecord] = {}

    def cached(self) -> list[ProductRecord]:
        return [r for r in self._by_id.values() if r.parent_id is None]

    def all_records(self) -> list[ProductRecord]:
        return list(self._by_id.values())

    def get_cached(self, listing_id: str) -> ProductRecord | None:
        return self._by_id.get(listing_id)

    def by_number(self, product_number: str) -> ProductRecord | None:
        return self._by_number.get(product_number)

    async def refresh(self) -> list[ProductRecord]:
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            result = await self._admin.search(
                "product",
                {
                    "includes": {"product": PRODUCT_FIELDS},
                    "associations": {"tax": {}, "categories": {}},
                    "sort": [{"field": "productNumber", "order": "ASC"}],
                    # A third-party seller's products are not this merchant's.
                    "filter": [not_third_party()],
                },
                limit=CATALOG_PAGE_SIZE,
                page=page,
            )
            rows.extend(result.rows)
            if not result.rows or len(rows) >= result.total:
                break
            page += 1
        records = [record_from_row(row) for row in rows]
        self._by_id = {record.listing_id: record for record in records}
        self._by_number = {r.product_number: r for r in records if r.product_number}
        for record in records:
            if record.parent_id and record.parent_id in self._by_id:
                parent = self._by_id[record.parent_id]
                record.parent = parent
                parent.children = [c for c in parent.children if c.listing_id != record.listing_id]
                parent.children.append(record)
        for record in records:
            record.children.sort(key=lambda c: c.product_number)
        return self.cached()

    async def get(self, listing_id: str) -> ProductRecord | None:
        if listing_id not in self._by_id:
            await self.refresh()
        return self._by_id.get(listing_id)

    async def fresh(self, listing_id: str) -> dict[str, Any] | None:
        """The live row for ``listing_id`` (not the cache): what ``ChangeItem.before`` and
        every write payload are built from."""
        return await self._admin.read(
            "product",
            listing_id,
            {"associations": {"tax": {}}, "includes": {"product": PRODUCT_FIELDS}},
        )

    def all_listings(self) -> list[Listing]:
        return [row.to_listing() for row in self.cached()]

    def stock_rows(self) -> list[ProductRecord]:
        """The rows stock lives on: every variant, and each plain product."""
        rows: list[ProductRecord] = []
        for record in self.cached():
            rows.extend(record.children or [record])
        return rows


def record_from_row(row: dict[str, Any]) -> ProductRecord:
    tax = row.get("tax") if isinstance(row.get("tax"), dict) else None
    tax_rate = None
    if tax is not None and tax.get("taxRate") is not None:
        tax_rate = float(tax["taxRate"])
    categories = row.get("categories") or []
    category = None
    if categories and isinstance(categories[0], dict):
        category = _translated(categories[0], "name")
    price_entries = row.get("price") if isinstance(row.get("price"), list) else None
    return ProductRecord(
        listing_id=str(row.get("id")),
        title=str(_translated(row, "name") or row.get("productNumber") or row.get("id")),
        product_number=str(row.get("productNumber") or ""),
        own_price=_gross(price_entries),
        stock=int(
            row.get("availableStock")
            if row.get("availableStock") is not None
            else row.get("stock") or 0
        ),
        active=bool(row.get("active", True)),
        parent_id=row.get("parentId"),
        description=_translated(row, "description"),
        meta_title=_translated(row, "metaTitle"),
        meta_description=_translated(row, "metaDescription"),
        tax_id=row.get("taxId"),
        tax_rate=tax_rate,
        price_entries=price_entries,
        purchase_price=_gross(row.get("purchasePrices")),
        category=category,
        category_ids=list(row.get("categoryIds") or []),
    )
