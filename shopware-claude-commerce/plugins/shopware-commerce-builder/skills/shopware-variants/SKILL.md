---
name: shopware-variants
description: Shopware's parent and child product model (configuratorSettings, options and property groups, inheritance, childCount, parentId) and how it becomes the blueprint's family with options and variants, covering the Store API reads, the REST quirk on family ids, variant resolution for cart writes, out-of-stock siblings, price and stock inheritance on the merchant side, and large families. Load when a product with sizes, colors, or other options is searched, presented, added to a cart, or priced against a Shopware shop.
---

# Variants on Shopware

Paths are in the Shopware reference repo: `storefront/api/shopware_backend.py` and
`store_api.py` for the shopper, `merchant/api/catalog.py` and `staging.py` for the merchant. The
blueprint's family shape (`ProductDetails.options`, `variants[]` each with `option_values` and
`variant_of`; cart writes take a variant, gates hold a family) is Anthropic's `docs/backends.md`
and its `commerce-architecture` skill.

## The Shopware model

- A **parent** product (`childCount > 0`, `parentId` null) is the family. Its
  `configuratorSettings` list the property-group options offered (size S, M, L; color); the
  parent itself is not sellable and has no stock of its own.
- Each **child** (`parentId` set) is one combination of `options`, each option a
  `property_group_option` with its `group` (`options.group.name` is the axis, `options.name` the
  value). A child inherits every field it leaves null (`name`, `price`, `deliveryTime`, `cover`,
  `active`, `tax`) from the parent, and overrides the ones it sets (`price` per variant,
  `stock`, `availableStock`, `isCloseout`, `ean`, `productNumber`).
- Ids are UUIDs, distinct for parent and child; the child carries `parentId`, the parent
  `childCount`. Nothing else links them, so a lookup by child id must read `parentId` to find its
  family.
- The Store API resolves a **family id to its best child** on `GET /store-api/product/{id}`
  (`parentId == requested id` on the answer is how the caller knows the request was the family);
  `POST /store-api/product` with a `parentId` filter lists every child, sold-out ones included,
  with `options.group`, `calculatedPrice`, `deliveryTime`, `unit`, and `referencePrice`. The UCP
  document lists a family with `variants[]` but no option matrix and no stock.

## Shopper: family, options, variants

- `get_product_details` on a family id: the UCP record (`catalog.product`, disambiguated with
  `catalog.lookup` and the Store API when the shop answers a child for a family; `_fetch_product_record`)
  gives title, description, media, price range; `_enrich_variants` reads the children through the
  Store API (`child_products`, capped by `CHILD_PRODUCTS_LIMIT`), maps each to a `Product` with
  `option_values` (`{group name: option name}`), `variant_of` the family id, `in_stock` from
  `available` and `availableStock`, and the family's `deliveryTime` into `specs`. `options` is the
  matrix collected from the children (`_options_of`), or from the UCP `options` spec when the
  document carries one (`_family_options`). The family's `variant_of` map remembers every child.
- `get_product_details` on a child id answers that variant's own details (its price, options,
  stock) with the family's description and specs (`_variant_details`), and files the child under
  its family, so a search result that listed a child still renders as part of one family.
- `search_products` returns families, not one card per child; a family's `in_stock` is true when
  any child is; its `price` is the range minimum. A card per child is wrong.
- `add_to_cart` with a family id: `_resolve_variant` picks the child the conversation named (the
  option the model chose from `options`), else the session's default variant, else the backend's;
  the family row itself is refused. `_assert_available` checks the child before the write; a
  sold-out child raises `Unavailable` with the in-stock siblings' ids in the message, so the
  model offers them by name and size rather than failing silently. `update_cart_item` and
  `remove_from_cart` accept the child already on the line, or map a family id to the line's child.
- A request naming no option on a multi-option family is a question back to the customer or the
  in-stock default, never a guess written to the cart; the eval suite pins both.
- Large families: a fence has a character cap. A family past about sixty children is split by its
  leading option (color first, then sizes) or `max_fenced_chars` is raised in the config; the
  decision record states the largest family's count.

## Merchant: inheritance, prices, stock

- `CatalogCache` (`merchant/api/catalog.py`) loads `product` rows with `parentId`, `childCount`,
  `price`, `purchasePrices`, `tax`, `stock`, `availableStock`, `active`, `options` and their
  groups, into `ProductRecord`s: a family with `children`, each child knowing its `own_price`
  (null when inheriting) and its parent. `Listing.options` is the family's matrix; `search_listings`
  answers families.
- `stage_price_update`: `_price_targets` decides where a write lands. A family whose children all
  inherit is priced on the parent row; a family with per-variant prices is repriced per variant,
  plus the parent for the children still inheriting; a child or a plain product on itself. The
  blueprint gate holds a family id that carries options, so the model names children when the
  variants differ in price. Net is recomputed from the tax rate, the parent's when the child
  inherits (shopware-promotions).
- `stage_inventory_action`: a restock on a family raises `ChangeNotApplicable` naming the
  children's product numbers, since stock lives on children; pausing or activating a family sets
  `active` on the parent and every child, the note says how many, and the preview lists each.
- `get_inventory_alerts` thresholds (`thresholds.json`, per product number with a default) apply
  to every row with stock, children included; a child's alert carries its product number so the
  operator can name it.
- `stage_promotion` expands a family to all its children's ids for the rule's `identifiers`, so
  the discount applies to whichever size is in the cart.

## Do not

- Present one card per child, or write a family id to the cart, a price, or a stock field.
- Trust `GET /ucp/v1/catalog/product/{id}` or `GET /store-api/product/{id}` to answer the id
  asked for; read `parentId` on the answer.
- Report a family in stock because the parent row says so; the children carry stock.
- Fill `option_values` from `properties` (descriptive attributes) instead of `options` (the
  configurator axes); properties go in `specs`.
- Guess a size or a color when the customer named none.
