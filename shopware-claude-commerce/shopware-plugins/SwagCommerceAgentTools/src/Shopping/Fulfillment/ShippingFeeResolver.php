<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Fulfillment;

use Shopware\Core\Checkout\Cart\Cart;
use Shopware\Core\Checkout\Cart\Delivery\DeliveryCalculator;
use Shopware\Core\Checkout\Cart\Price\Struct\CartPrice;
use Shopware\Core\Checkout\Shipping\Aggregate\ShippingMethodPrice\ShippingMethodPriceEntity;
use Shopware\Core\Checkout\Shipping\ShippingMethodEntity;
use Shopware\Core\Defaults;
use Shopware\Core\System\SalesChannel\SalesChannelContext;

/**
 * Resolves the shipping fee of a shipping method for the current sales-channel context.
 *
 * Exact fee: when the cart already carries a calculated delivery for the method
 * (the context's selected method), its shipping costs are authoritative.
 *
 * Estimated fee: otherwise the shipping-method price matrix is evaluated the
 * same way DeliveryCalculator does for rule matching (calculation rule and
 * availability rule must match the context), but without cart quantities.
 * The first matching tier (lowest quantityStart) is used, which is the correct
 * answer for a single-item enquiry and a documented estimate otherwise.
 */
class ShippingFeeResolver
{
    public function resolve(ShippingMethodEntity $method, SalesChannelContext $context, ?Cart $cart = null): ?ShippingFee
    {
        $currencyIso = $context->getCurrency()->getIsoCode();

        if ($cart !== null) {
            foreach ($cart->getDeliveries() as $delivery) {
                if ($delivery->getShippingMethod()->getId() === $method->getId()) {
                    return new ShippingFee($delivery->getShippingCosts()->getTotalPrice(), $currencyIso, false);
                }
            }
        }

        $tier = $this->selectTier($method, $context);
        if ($tier === null) {
            return null;
        }

        $prices = $tier->getCurrencyPrice();
        if ($prices === null) {
            return null;
        }

        $price = $prices->getCurrencyPrice($context->getCurrencyId());
        if ($price === null) {
            return null;
        }

        $value = $this->isNetContext($context) ? $price->getNet() : $price->getGross();
        if ($price->getCurrencyId() === Defaults::CURRENCY && $context->getCurrencyId() !== Defaults::CURRENCY) {
            $value *= $context->getContext()->getCurrencyFactor();
        }

        return new ShippingFee($value, $currencyIso, true);
    }

    private function selectTier(ShippingMethodEntity $method, SalesChannelContext $context): ?ShippingMethodPriceEntity
    {
        $ruleIds = $context->getRuleIds();
        $candidates = [];

        foreach ($method->getPrices() as $tier) {
            $calculationRuleId = $tier->getCalculationRuleId();
            if ($calculationRuleId !== null && !\in_array($calculationRuleId, $ruleIds, true)) {
                continue;
            }

            $ruleId = $tier->getRuleId();
            if ($ruleId !== null && !\in_array($ruleId, $ruleIds, true)) {
                continue;
            }

            $candidates[] = $tier;
        }

        if ($candidates === []) {
            return null;
        }

        usort($candidates, static function (ShippingMethodPriceEntity $a, ShippingMethodPriceEntity $b): int {
            // Rule-priced tiers are more specific than the default matrix.
            $specificity = ($b->getCalculationRuleId() !== null ? 1 : 0) <=> ($a->getCalculationRuleId() !== null ? 1 : 0);
            if ($specificity !== 0) {
                return $specificity;
            }

            $byLineItemCount = ($a->getCalculation() === DeliveryCalculator::CALCULATION_BY_LINE_ITEM_COUNT ? 0 : 1)
                <=> ($b->getCalculation() === DeliveryCalculator::CALCULATION_BY_LINE_ITEM_COUNT ? 0 : 1);
            if ($byLineItemCount !== 0) {
                return $byLineItemCount;
            }

            return ($a->getQuantityStart() ?? 0.0) <=> ($b->getQuantityStart() ?? 0.0);
        });

        return $candidates[0];
    }

    private function isNetContext(SalesChannelContext $context): bool
    {
        return \in_array($context->getTaxState(), [CartPrice::TAX_STATE_NET, CartPrice::TAX_STATE_FREE], true);
    }
}
