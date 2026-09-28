---
name: shopware-promotions
description: Shopware's promotion model (promotion, discounts, rules, sales-channel binding, codes, priorities) and how the blueprint's PromotionDraft, price update, and pricing context map onto it, including the guardrail caps, the atomic nested payload, the dry-run preview, and what a cart-scope percentage discount does and does not do. Load when staging or reviewing a promotion or a price change against a Shopware shop, or when a pricing rule needs a home.
---

# Promotions and pricing on Shopware

Paths are in the Shopware reference repo: `merchant/api/staging.py` builds the payloads,
`merchant/api/shopware_backend.py` stages and applies, `merchant/api/insights.py` and
`merchant/data/pricing_policy.json` supply floors and margins. The blueprint's caps
(`max_price_delta_pct`, `max_promotion_discount_pct`, `max_items_per_change`) live in
`MerchantAgentConfig` and are enforced by its gates at stage and again at apply
(Anthropic's `commerce-merchant-operations` skill); this skill says what they are applied to.

## The Shopware model

| Entity | Role |
|---|---|
| `promotion` | The campaign record: `name`, `active`, `validFrom`, `validUntil`, `priority`, `exclusive`, `useCodes`, `useIndividualCodes`, `useSetGroups`, `maxRedemptionsGlobal`, `maxRedemptionsPerCustomer`, `orderCount` |
| `promotion_sales_channel` | Which channels the promotion runs in; without a row it applies nowhere |
| `promotion_discount` | One or more discounts: `scope` (`cart`, `delivery`, `set`, `setgroup`), `type` (`percentage`, `absolute`, `fixed`, `fixed_unit`), `value`, `maxValue`, `considerAdvancedRules`, `discountRules` |
| `promotion_discount_rule` | Binds a `rule` to a discount so it applies to the line items the rule matches |
| `rule`, `rule_condition` | The rule builder: a condition tree (`cartLineItem` with `identifiers`, `cartLineItemOfType`, `customerGroup`, `dateRange`, `cartCartAmount`, and so on); promotions also take `personaRules`, `cartRules`, `orderRules` for who, which cart, which order |
| `promotion_individual_code` | Generated codes when `useIndividualCodes` is on; not used by the reference |

A percentage discount in `cart` scope reduces the cart total; with `considerAdvancedRules` and a
`discountRules` rule on `cartLineItem.identifiers`, it applies only when the cart holds one of
those products, and the amount is computed on the matched line items. Advanced prices
(`product.prices` with a `rule`) are a different mechanism: rule-based list prices per customer
group or quantity, not promotions; the reference reads them in `get_pricing_context` and does not
write them.

## PromotionDraft to payload

`stage_promotion` takes the blueprint's `PromotionDraft` (`name`, `discount_pct`, `starts`,
`ends`, `listing_ids`) and:

1. Resolves each listing to its record; a family expands to all its children's ids, a single
   product to itself. The `items[]` show each listing's `price` `before` and the discounted
   `after` (`price * (1 - pct / 100)`, two decimals) as the preview, since Shopware computes the
   real amount per cart.
2. Builds **one nested payload** (`promotion_payload`): the `promotion` (`active: true`,
   `useCodes: false`, `priority: 1`, `exclusive: false`, `validFrom` at the start day's midnight
   UTC and `validUntil` at the end day's `23:59:59`, from `iso_window`), its `salesChannels`
   binding to `SHOPWARE_SALES_CHANNEL_ID`, one `discounts[]` entry (`scope: cart`, `type:
   percentage`, `value`, `considerAdvancedRules: true`), and under `discountRules` a new `rule`
   with a `cartLineItem` condition whose `identifiers` are the resolved product ids. New ids are
   generated in the host (`uuid4().hex`), so the same payload replays on apply.
3. Previews it with `shopware-entity-upsert` `dryRun: true`; Shopware writes the nested rule in
   the same transaction, so the dry run validates all of it and a failure stages nothing. The
   notes record the promotion id, the percentage, the scope, the window, the channel, and the
   rule id.
4. On `apply_change`, replays the payload with `dryRun: false`. `promotion` and `rule` are
   `CREATED_ENTITIES`: on a later failure in the same apply they are deleted again
   (`shopware-entity-delete`) and `WriteFailed` names what completed and what was rolled back.

Without a sales channel to bind to, `stage_promotion` raises `ChangeNotApplicable` naming the
variable. `stage_campaign` always raises it: Shopware core has no campaign object; a code
promotion is the nearest proxy and `get_campaign_performance` could read `promotion.orderCount`
and `order_line_item` rows of type `promotion` when a deployment wants it.

Known limits the notes state: one promotion per staged change; a cart-scope percentage on a
product rule (per-line-item scoping, `absolute` and `fixed` types, codes, redemption limits, and
customer-group persona rules are refinements a deployment adds as new payload fields, each with a
netless `FakeAdmin` case).

## Price updates

The blueprint gate holds a family id that carries options; a target that reaches
`stage_price_update` lands where `_price_targets` says: a plain product or a child on itself, a
family whose children all inherit on the parent row, a family with per-variant prices on each
priced variant plus the parent for the children still inheriting. For each row the current
`price` array is read fresh, only the entry for the sales channel's currency is replaced, `gross`
is the new price, `net = gross / (1 + taxRate / 100)` from the product's tax (the family's when
the child inherits, and the note says so), `linked` is kept, and every other currency entry is
left as it was. The preview compares `before` from the fresh read with `after` from the payload,
and the notes carry the margin before and after when `purchasePrices` gives a unit cost; the cap
`max_price_delta_pct` is checked against `before`. `get_pricing_context` reports `unit_cost`,
`margin_pct`, and `min_price` with its basis from `pricing_policy.json` (per product number, else
cost times the default minimum margin) so the model can refuse a move below the floor and say
why; a deployment that wants the floor enforced adds the check at stage time beside the whitelist.
Customer-group and quantity prices (`product.prices`) are reported, not written.

## Caps and guardrails

| Cap | Applied to |
|---|---|
| `max_promotion_discount_pct` | `discount_pct` of the draft, at stage and at apply |
| `max_price_delta_pct` | the relative move of each child's gross against the fresh `before` |
| `max_items_per_change` | listings in a promotion, targets in a price update |
| `protected_fields`, `price_bearing_fields` | a listing update may not carry `price`, `prices`, `purchasePrices`, `stock`, `active`; the whitelist in `LISTING_FIELDS` is stricter still |
| `min_price` from the policy file | reported in `get_pricing_context` with its basis; the model refuses below it and names the floor; enforcing it in code is a deployment's addition at stage time |

The defaults are demonstration values; a shop sets its own in the config and the policy file, and
the decision record says which.

## Do not

- Write `promotion_discount`, `rule`, and `promotion_sales_channel` as separate calls; one nested
  payload is one transaction and one dry run.
- Set `useCodes` or a code without a redemption limit the operator named.
- Preview a promotion's amount as a price change on the product; the preview shows the listing's
  discounted price and the note says the discount is computed per cart.
- Replace the whole `price` array, write a gross without its net, or move a price on the family
  row.
- Stage a "campaign" as a promotion silently; say the limitation and offer the promotion.
