<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Shopping\Fulfillment;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\TestCase;
use Shopware\Core\Checkout\Cart\Cart;
use Shopware\Core\Checkout\Cart\Delivery\DeliveryCalculator;
use Shopware\Core\Checkout\Cart\Delivery\Struct\Delivery;
use Shopware\Core\Checkout\Cart\Delivery\Struct\DeliveryCollection;
use Shopware\Core\Checkout\Cart\Delivery\Struct\DeliveryDate;
use Shopware\Core\Checkout\Cart\Delivery\Struct\DeliveryPositionCollection;
use Shopware\Core\Checkout\Cart\Delivery\Struct\ShippingLocation;
use Shopware\Core\Checkout\Cart\Price\Struct\CalculatedPrice;
use Shopware\Core\Checkout\Cart\Price\Struct\CartPrice;
use Shopware\Core\Checkout\Cart\Tax\Struct\CalculatedTaxCollection;
use Shopware\Core\Checkout\Cart\Tax\Struct\TaxRuleCollection;
use Shopware\Core\Checkout\Shipping\Aggregate\ShippingMethodPrice\ShippingMethodPriceCollection;
use Shopware\Core\Checkout\Shipping\Aggregate\ShippingMethodPrice\ShippingMethodPriceEntity;
use Shopware\Core\Checkout\Shipping\ShippingMethodEntity;
use Shopware\Core\Defaults;
use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\DataAbstractionLayer\Pricing\Price;
use Shopware\Core\Framework\DataAbstractionLayer\Pricing\PriceCollection;
use Shopware\Core\System\Country\CountryEntity;
use Shopware\Core\System\Currency\CurrencyEntity;
use Shopware\Core\System\DeliveryTime\DeliveryTimeEntity;
use Shopware\Core\System\SalesChannel\SalesChannelContext;
use Swag\CommerceAgentTools\Shopping\Fulfillment\DeliveryTimeFormatter;
use Swag\CommerceAgentTools\Shopping\Fulfillment\ShippingFee;
use Swag\CommerceAgentTools\Shopping\Fulfillment\ShippingFeeResolver;

/**
 * @internal
 */
#[CoversClass(ShippingFeeResolver::class)]
#[CoversClass(ShippingFee::class)]
#[CoversClass(DeliveryTimeFormatter::class)]
class ShippingFeeResolverTest extends TestCase
{
    private const METHOD_ID = '0190dddd000000000000000000000001';
    private const RULE_ID = '0190eeee000000000000000000000002';
    private const CHF_ID = '0190ffff000000000000000000000003';

    private ShippingFeeResolver $resolver;

    protected function setUp(): void
    {
        $this->resolver = new ShippingFeeResolver();
    }

    public function testCartDeliveryIsAuthoritativeWhenPresent(): void
    {
        $method = $this->method([$this->tier(4.9, 4.12)]);
        $cart = new Cart('token');
        $cart->setDeliveries(new DeliveryCollection([$this->delivery($method, 0.0)]));

        $fee = $this->resolver->resolve($method, $this->context(), $cart);

        static::assertNotNull($fee);
        static::assertSame(['amount' => 0.0, 'currency' => 'EUR', 'estimated' => false], $fee->toArray());
    }

    public function testEstimatesFromMatrixWhenNoCartDelivery(): void
    {
        $fee = $this->resolver->resolve($this->method([$this->tier(4.9, 4.12)]), $this->context());

        static::assertNotNull($fee);
        static::assertSame(['amount' => 4.9, 'currency' => 'EUR', 'estimated' => true], $fee->toArray());
    }

    public function testNetContextUsesNetPrice(): void
    {
        $fee = $this->resolver->resolve($this->method([$this->tier(4.9, 4.12)]), $this->context(taxState: CartPrice::TAX_STATE_NET));

        static::assertNotNull($fee);
        static::assertSame(4.12, $fee->toArray()['amount']);
    }

    public function testTiersWithNonMatchingRulesAreSkipped(): void
    {
        $foreignRule = '0190aaaa00000000000000000000000f';
        $method = $this->method([
            $this->tier(0.0, 0.0, calculationRuleId: $foreignRule),
            $this->tier(9.9, 8.32, ruleId: $foreignRule),
            $this->tier(4.9, 4.12),
        ]);

        $fee = $this->resolver->resolve($method, $this->context());

        static::assertNotNull($fee);
        static::assertSame(4.9, $fee->toArray()['amount']);
    }

    public function testMatchingCalculationRuleTierWinsOverDefaultMatrix(): void
    {
        $method = $this->method([
            $this->tier(4.9, 4.12),
            $this->tier(0.0, 0.0, calculationRuleId: self::RULE_ID),
        ]);

        $fee = $this->resolver->resolve($method, $this->context(ruleIds: [self::RULE_ID]));

        static::assertNotNull($fee);
        static::assertSame(0.0, $fee->toArray()['amount']);
    }

    public function testLowestQuantityTierIsPickedWithoutCart(): void
    {
        $method = $this->method([
            $this->tier(2.9, 2.44, quantityStart: 5.0),
            $this->tier(4.9, 4.12, quantityStart: 1.0),
        ]);

        $fee = $this->resolver->resolve($method, $this->context());

        static::assertNotNull($fee);
        static::assertSame(4.9, $fee->toArray()['amount']);
    }

    public function testDefaultCurrencyPriceIsConvertedWithFactorForOtherCurrencies(): void
    {
        $fee = $this->resolver->resolve($this->method([$this->tier(10.0, 8.4)]), $this->context(currencyId: self::CHF_ID, currencyIso: 'CHF', factor: 1.1));

        static::assertNotNull($fee);
        static::assertSame(['amount' => 11.0, 'currency' => 'CHF', 'estimated' => true], $fee->toArray());
    }

    public function testNoMatchingTierYieldsNull(): void
    {
        static::assertNull($this->resolver->resolve($this->method([]), $this->context()));
    }

    public function testDeliveryTimeFormatterAndWidening(): void
    {
        $formatter = new DeliveryTimeFormatter();

        static::assertNull($formatter->format(null));

        $fast = $this->deliveryTime(1, 3, DeliveryTimeEntity::DELIVERY_TIME_DAY, '1-3 Tage');
        $slow = $this->deliveryTime(2, 5, DeliveryTimeEntity::DELIVERY_TIME_DAY, '2-5 Tage');
        $weeks = $this->deliveryTime(1, 2, DeliveryTimeEntity::DELIVERY_TIME_WEEK, '1-2 Wochen');

        static::assertSame(['min' => 1, 'max' => 3, 'unit' => 'day', 'text' => '1-3 Tage'], $formatter->format($fast));

        $widened = $formatter->widen($formatter->format($fast), $formatter->format($slow));
        static::assertSame(['min' => 1, 'max' => 5, 'unit' => 'day', 'text' => null], $widened);

        static::assertSame($formatter->format($fast), $formatter->widen($formatter->format($fast), null));
        static::assertSame($formatter->format($fast), $formatter->widen(null, $formatter->format($fast)));
        static::assertSame($formatter->format($weeks), $formatter->widen($formatter->format($fast), $formatter->format($weeks)), 'mixed units keep the slower estimate');
    }

    /**
     * @param list<ShippingMethodPriceEntity> $tiers
     */
    private function method(array $tiers): ShippingMethodEntity
    {
        $method = new ShippingMethodEntity();
        $method->setId(self::METHOD_ID);
        $method->setPrices(new ShippingMethodPriceCollection($tiers));

        return $method;
    }

    private function tier(float $gross, float $net, ?string $ruleId = null, ?string $calculationRuleId = null, ?float $quantityStart = null): ShippingMethodPriceEntity
    {
        static $counter = 0;
        $tier = new ShippingMethodPriceEntity();
        $tier->setId(\sprintf('%032x', ++$counter));
        $tier->setShippingMethodId(self::METHOD_ID);
        if ($ruleId !== null) {
            $tier->setRuleId($ruleId);
        }
        if ($calculationRuleId !== null) {
            $tier->setCalculationRuleId($calculationRuleId);
        }
        $tier->setCalculation(DeliveryCalculator::CALCULATION_BY_LINE_ITEM_COUNT);
        if ($quantityStart !== null) {
            $tier->setQuantityStart($quantityStart);
        }
        $tier->setCurrencyPrice(new PriceCollection([new Price(Defaults::CURRENCY, $net, $gross, false)]));

        return $tier;
    }

    /**
     * @param list<string> $ruleIds
     */
    private function context(string $taxState = CartPrice::TAX_STATE_GROSS, array $ruleIds = [], string $currencyId = Defaults::CURRENCY, string $currencyIso = 'EUR', float $factor = 1.0): SalesChannelContext
    {
        $currency = new CurrencyEntity();
        $currency->setId($currencyId);
        $currency->setIsoCode($currencyIso);
        $currency->setFactor($factor);

        $context = $this->createMock(SalesChannelContext::class);
        $context->method('getCurrency')->willReturn($currency);
        $context->method('getCurrencyId')->willReturn($currencyId);
        $context->method('getTaxState')->willReturn($taxState);
        $context->method('getRuleIds')->willReturn($ruleIds);
        $context->method('getContext')->willReturn(new Context(new \Shopware\Core\Framework\Api\Context\SystemSource(), [], $currencyId, [Defaults::LANGUAGE_SYSTEM], Defaults::LIVE_VERSION, $factor));

        return $context;
    }

    private function delivery(ShippingMethodEntity $method, float $cost): Delivery
    {
        $country = new CountryEntity();
        $country->setId('0190aaaa00000000000000000000000c');
        $price = new CalculatedPrice($cost, $cost, new CalculatedTaxCollection(), new TaxRuleCollection());
        $date = new DeliveryDate(new \DateTimeImmutable('2026-09-05'), new \DateTimeImmutable('2026-09-08'));

        return new Delivery(new DeliveryPositionCollection(), $date, $method, new ShippingLocation($country, null, null), $price);
    }

    private function deliveryTime(int $min, int $max, string $unit, string $name): DeliveryTimeEntity
    {
        $deliveryTime = new DeliveryTimeEntity();
        $deliveryTime->setId(\sprintf('%032x', $min * 100 + $max));
        $deliveryTime->setMin($min);
        $deliveryTime->setMax($max);
        $deliveryTime->setUnit($unit);
        $deliveryTime->setTranslated(['name' => $name]);

        return $deliveryTime;
    }
}
