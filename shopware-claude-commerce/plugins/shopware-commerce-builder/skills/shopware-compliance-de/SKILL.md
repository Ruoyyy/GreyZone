---
name: shopware-compliance-de
description: The German consumer-law disclosures a shopping agent owes on every product it presents (PAngV base price, delivery time, VAT wording, shipping-cost hint, Widerruf) and how the reference authors them on the server from Shopware fields and fixed copy, grounds policy answers in the shop's own CMS pages, and pins them byte-exact in evals. Load when presenting prices, delivery times, or terms to a customer of a German or Austrian Shopware shop, or when reviewing a disclosure or policy path.
---

# German disclosures on Shopware

Paths are in the Shopware reference repo: `storefront/api/disclosures.py` authors the rows,
`storefront/data/disclosure_copy.de.json` holds the copy, `storefront/api/policies.py` indexes
the shop's pages, `docker/seed_catalog.py` seeds the demo pages and a base-price product, and
`evals/scorers.py` has the byte-exact graders. The blueprint's `present_disclosure` tool and
`StorefrontBackend.get_disclosure` (registered with `enable_disclosures`, on in
`storefront/api/agent_config.py`) carry them; the blueprint fills the card from the server
record and the model authors nothing on it (Anthropic's `commerce-ui-tools` skill).

## What is owed

| Obligation | Source in German law | What the customer must see |
|---|---|---|
| Base price (Grundpreis) | PAngV § 4: goods sold by weight, volume, length, or area show the price per unit (per kilogram, liter, meter, square meter; per 100 g or 100 ml where customary) next to the total price | `12,90 € / 1 l` beside `6,45 €`, on the card and in the cart |
| Total price includes VAT | PAngV § 3 and § 6: the price is the gross price, and the text says so | `inkl. MwSt.` |
| Shipping costs | PAngV § 6: whether and how much shipping costs, or how it is computed | `zzgl. Versandkosten, berechnet im Checkout.` or the amount |
| Delivery time | BGB § 312d with Art. 246a EGBGB: the date or period by which the goods arrive | `Lieferzeit: 2-4 Tage` from the product's `deliveryTime` |
| Right of withdrawal (Widerruf) | BGB § 312g, § 355: fourteen days, the Widerrufsbelehrung and the model form | answered from the shop's own Widerrufsbelehrung page |
| Availability | UWG: no offer of goods that cannot be delivered in a reasonable time | `Derzeit nicht lieferbar` when stock is gone |

The wording is the shop's legal responsibility, so it is copy the merchant reviewed, never text
the model produced.

## How the reference authors them

- `get_disclosure(product_id)` reads the Store API product (the family when the child id yields
  nothing) and `disclosure_from_store_product` builds `Disclosure` rows in a fixed order:
  `Grundpreis` from `calculatedPrice.referencePrice` (`price` in German number format with the
  euro sign, `/ unitName`), `Lieferzeit` from `deliveryTime` (`name`, and `(min–max Tage)` when
  both bounds are set), `Verfügbarkeit` from `available` and `availableStock`, `Preis` with the
  VAT copy, `Versand` with the shipping hint. `sources` names `shopware-store-api`.
- The labels and the fixed sentences come from `disclosure_copy.de.json` over the defaults in the
  module; a shop edits that file, and a second language is a second file selected by the sales
  channel's language. The model sees the rows inside the fence and the card renders them; it
  cannot rephrase a row.
- `referencePrice` exists only when the product has a `unit`, a `purchaseUnit`, and a
  `referenceUnit`; the seed's olive oil (`CA-OIL`) carries them, the T-shirts do not. A product
  without a base price shows no `Grundpreis` row, and the model does not compute one.
- The static prompt (`domain_search_notes` in `agent_config.py`) tells the model that the German
  mandatory facts (Grundpreis, delivery time, VAT) come from `get_disclosure` and not from the
  product description, and that checkout, shipping, and payment happen on the shop's own page;
  the `present_disclosure` tool description says when the card is owed. The model never types the
  figures itself.
- `get_fulfillment_options` renders each shipping method with its fee from
  `prices[].currencyPrice` and its `deliveryTime`, and the product's own delivery time as the
  availability; the checkout card carries the shipping hint, and the amount is Shopware's in
  `/checkout/confirm`.

## Policies from the shop's own pages

- `PolicyIndex.rebuild` walks the sales channel's footer and service navigation, reads each CMS
  category's text slots, strips HTML, caps each page at `MAX_POLICY_CHARS`, and classifies it by
  title needles (`widerruf`, `rückgabe`, `retoure` → `returns`; `versand`, `liefer` → `shipping`;
  `agb` → `terms`; `datenschutz` → `privacy`; `kontakt`, `impressum` → `contact`). `/agents.md` and
  `/llms.txt` are appended when the shop serves them; the fallback copy in the module is used only
  when the shop exposes no page at all, and `live` says which is in force.
- A terms question is grounded: the blueprint forces `search_policies` first
  (`policy_intent_terms`), and the answer quotes the page's period (`14 Tagen`), fee (`4,90 €`), or
  clause, never the model's memory of German law. The seed's pages (`Widerrufsbelehrung /
  Rückgabe`, `Versand & Lieferzeit`, `AGB`, `Datenschutz`, `Kontakt`) make this testable locally.
- The shop's terms are the shop's; when the agent runs for a marketplace channel, `search_policies`
  results state whose terms they are.

## Pinning it in evals

- `byte_exact_disclosure`: the `disclosure` component's `rows` and `title`, serialized with sorted
  keys, equal the backend's own `get_disclosure` record for that product, byte for byte. A case
  never spells the row; the server record is the oracle, so a copy change does not break a case.
- `disclosure_row`: a row with `label: Grundpreis` (and a `value` when the case pins one from the
  seed) is present; `label: Lieferzeit` for the delivery time.
- `grounded_numbers`: every figure with a decimal part or a currency or percent sign in the reply
  appears in a tool result, a component payload, or the customer's own text; a delivery time or a
  price the model made up fails.
- `text_not_contains` on order-placed wording (`bestellt`, `placed`, `Bestellung aufgegeben`) on
  the checkout case, and `no_urls_in_text` so the handoff URL stays in the card.
- The negative of every disclosure case: a product without a base price shows no `Grundpreis`
  row (`ui_payload_has` inverted through the row grader), and a browsing turn with no price
  renders no disclosure (`ui_component_not_rendered: disclosure`).

## Do not

- Let the model write, translate, round, or reorder a disclosure row; change the copy file instead.
- Compute a base price in the host from price and content; use `referencePrice`, and show no row
  without it.
- Answer a Widerruf, shipping, or VAT question from general knowledge; read the page.
- Show a net or customer-group price to a guest; the Store API context decides what
  `calculatedPrice` is.
- Report an order as placed; the agent hands off to Shopware's checkout, where the legal order
  confirmation happens.
