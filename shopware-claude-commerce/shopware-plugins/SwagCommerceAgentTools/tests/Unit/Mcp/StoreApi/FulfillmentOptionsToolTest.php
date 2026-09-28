<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Mcp\StoreApi;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\TestCase;
use Shopware\Core\Checkout\Cart\CartException;
use Shopware\Core\Checkout\Cart\Delivery\DeliveryCalculator;
use Shopware\Core\Checkout\Cart\Price\Struct\CartPrice;
use Shopware\Core\Checkout\Cart\SalesChannel\CartService;
use Shopware\Core\Checkout\Shipping\Aggregate\ShippingMethodPrice\ShippingMethodPriceCollection;
use Shopware\Core\Checkout\Shipping\Aggregate\ShippingMethodPrice\ShippingMethodPriceEntity;
use Shopware\Core\Checkout\Shipping\SalesChannel\AbstractShippingMethodRoute;
use Shopware\Core\Checkout\Shipping\SalesChannel\ShippingMethodRouteResponse;
use Shopware\Core\Checkout\Shipping\ShippingMethodCollection;
use Shopware\Core\Checkout\Shipping\ShippingMethodEntity;
use Shopware\Core\Content\Product\ProductCollection;
use Shopware\Core\Content\Product\SalesChannel\AbstractProductListRoute;
use Shopware\Core\Content\Product\SalesChannel\ProductListResponse;
use Shopware\Core\Content\Product\SalesChannel\SalesChannelProductEntity;
use Shopware\Core\Defaults;
use Shopware\Core\Framework\Api\Context\SystemSource;
use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\DataAbstractionLayer\Pricing\Price;
use Shopware\Core\Framework\DataAbstractionLayer\Pricing\PriceCollection;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Criteria;
use Shopware\Core\Framework\DataAbstractionLayer\Search\EntitySearchResult;
use Shopware\Core\Framework\Mcp\Context\StoreApiMcpContextProvider;
use Shopware\Core\System\Currency\CurrencyEntity;
use Shopware\Core\System\DeliveryTime\DeliveryTimeEntity;
use Shopware\Core\System\SalesChannel\SalesChannelContext;
use Swag\CommerceAgentTools\Mcp\StoreApi\FulfillmentOptionsTool;
use Swag\CommerceAgentTools\Shopping\Fulfillment\DeliveryTimeFormatter;
use Swag\CommerceAgentTools\Shopping\Fulfillment\ShippingFeeResolver;

/**
 * @internal
 */
#[CoversClass(FulfillmentOptionsTool::class)]
class FulfillmentOptionsToolTest extends TestCase
{
    private const PRODUCT_ID = '0190c1b2d3e4f5a6b7c8d9e0f1a2b3c4';
    private const STANDARD_ID = '0190dddd000000000000000000000001';
    private const EXPRESS_ID = '0190dddd000000000000000000000002';
    private const SALES_CHANNEL_ID = '0190eeee000000000000000000000001';

    public function testOptionsCarryTheCarriersOwnTimeNextToTheProductsAvailability(): void
    {
        // Live finding (6.7.13, seeded shop): the product ships in 2-4 days, Express delivers
        // in 1-2. The option ETA is the availability window; without shippingTime the agent
        // could not tell Express from Standard.
        $result = json_decode($this->tool()->__invoke(self::PRODUCT_ID), true, 512, \JSON_THROW_ON_ERROR);

        static::assertTrue($result['success']);
        $options = $result['data']['options'];
        static::assertIsArray($options);
        static::assertCount(2, $options);

        $standard = $options[0];
        $express = $options[1];
        static::assertSame('Standard', $standard['name']);
        static::assertTrue($standard['selected']);
        static::assertSame('2-4 Tage', $standard['eta']['text']);
        static::assertSame('2-4 Tage', $standard['shippingTime']['text']);
        static::assertSame(['amount' => 4.9, 'currency' => 'EUR', 'estimated' => true], $standard['fee']);

        static::assertSame('Express', $express['name']);
        static::assertFalse($express['selected']);
        static::assertSame('2-4 Tage', $express['eta']['text'], 'the product still needs 2-4 days to leave the shop');
        static::assertSame(['min' => 1, 'max' => 2, 'unit' => 'day', 'text' => '1-2 Tage'], $express['shippingTime']);
        static::assertSame(9.9, $express['fee']['amount']);

        static::assertSame([self::PRODUCT_ID], array_column($express['products'], 'productId'));
        static::assertSame([], $result['_meta']['unknownProductIds']);
        static::assertSame(self::STANDARD_ID, $result['_meta']['selectedShippingMethodId']);
    }

    public function testAMethodWithoutDeliveryTimeReportsNullShippingTime(): void
    {
        $result = json_decode($this->tool(expressHasDeliveryTime: false)->__invoke(self::PRODUCT_ID), true, 512, \JSON_THROW_ON_ERROR);

        $express = $result['data']['options'][1];
        static::assertNull($express['shippingTime']);
        static::assertSame('2-4 Tage', $express['eta']['text']);
    }

    public function testUnknownProductsAreRefused(): void
    {
        $tool = $this->tool(products: []);

        $result = json_decode($tool->__invoke(self::PRODUCT_ID), true, 512, \JSON_THROW_ON_ERROR);

        static::assertFalse($result['success']);
        static::assertStringContainsString('None of the given products', $result['error']);
    }

    /**
     * @param list<SalesChannelProductEntity>|null $products
     */
    private function tool(?array $products = null, bool $expressHasDeliveryTime = true): FulfillmentOptionsTool
    {
        $context = $this->context();
        $products ??= [$this->product()];

        $contextProvider = $this->createMock(StoreApiMcpContextProvider::class);
        $contextProvider->method('getSalesChannelContext')->willReturn($context);

        $productRoute = $this->createMock(AbstractProductListRoute::class);
        $productRoute->method('load')->willReturn(new ProductListResponse(
            new EntitySearchResult('product', \count($products), new ProductCollection($products), null, new Criteria(), $context->getContext()),
        ));

        $express = $this->method(self::EXPRESS_ID, 'Express', 9.9, $expressHasDeliveryTime ? $this->deliveryTime(1, 2, '1-2 Tage') : null);
        $methods = new ShippingMethodCollection([
            $this->method(self::STANDARD_ID, 'Standard', 4.9, $this->deliveryTime(2, 4, '2-4 Tage')),
            $express,
        ]);
        $shippingRoute = $this->createMock(AbstractShippingMethodRoute::class);
        $shippingRoute->method('load')->willReturn(new ShippingMethodRouteResponse(
            new EntitySearchResult('shipping_method', 2, $methods, null, new Criteria(), $context->getContext()),
        ));

        $cartService = $this->createMock(CartService::class);
        $cartService->method('getCart')->willThrowException(CartException::tokenNotFound('token'));

        return new FulfillmentOptionsTool(
            $contextProvider,
            $shippingRoute,
            $productRoute,
            $cartService,
            new ShippingFeeResolver(),
            new DeliveryTimeFormatter(),
        );
    }

    private function context(): SalesChannelContext
    {
        $currency = new CurrencyEntity();
        $currency->setId(Defaults::CURRENCY);
        $currency->setIsoCode('EUR');
        $currency->setFactor(1.0);

        $selected = new ShippingMethodEntity();
        $selected->setId(self::STANDARD_ID);

        $context = $this->createMock(SalesChannelContext::class);
        $context->method('getCurrency')->willReturn($currency);
        $context->method('getCurrencyId')->willReturn(Defaults::CURRENCY);
        $context->method('getTaxState')->willReturn(CartPrice::TAX_STATE_GROSS);
        $context->method('getRuleIds')->willReturn([]);
        $context->method('getToken')->willReturn('token');
        $context->method('getSalesChannelId')->willReturn(self::SALES_CHANNEL_ID);
        $context->method('getShippingMethod')->willReturn($selected);
        $context->method('getContext')->willReturn(new Context(new SystemSource(), [], Defaults::CURRENCY, [Defaults::LANGUAGE_SYSTEM]));

        return $context;
    }

    private function product(): SalesChannelProductEntity
    {
        $product = new SalesChannelProductEntity();
        $product->setId(self::PRODUCT_ID);
        $product->setUniqueIdentifier(self::PRODUCT_ID);
        $product->setTranslated(['name' => 'Extra Virgin Olive Oil 500 ml']);
        $product->setShippingFree(false);
        $product->setDeliveryTime($this->deliveryTime(2, 4, '2-4 Tage'));

        return $product;
    }

    private function method(string $id, string $name, float $gross, ?DeliveryTimeEntity $deliveryTime): ShippingMethodEntity
    {
        $tier = new ShippingMethodPriceEntity();
        $tier->setId(substr($id, 0, 24) . 'aaaaaaaa');
        $tier->setUniqueIdentifier($tier->getId());
        $tier->setShippingMethodId($id);
        $tier->setCalculation(DeliveryCalculator::CALCULATION_BY_LINE_ITEM_COUNT);
        $tier->setQuantityStart(1.0);
        $tier->setCurrencyPrice(new PriceCollection([new Price(Defaults::CURRENCY, round($gross / 1.19, 2), $gross, false)]));

        $method = new ShippingMethodEntity();
        $method->setId($id);
        $method->setUniqueIdentifier($id);
        $method->setTranslated(['name' => $name, 'description' => null]);
        $method->setPrices(new ShippingMethodPriceCollection([$tier]));
        if ($deliveryTime !== null) {
            $method->setDeliveryTime($deliveryTime);
        }

        return $method;
    }

    private function deliveryTime(int $min, int $max, string $name): DeliveryTimeEntity
    {
        $deliveryTime = new DeliveryTimeEntity();
        $deliveryTime->setId(\sprintf('%032x', $min * 100 + $max));
        $deliveryTime->setMin($min);
        $deliveryTime->setMax($max);
        $deliveryTime->setUnit('day');
        $deliveryTime->setTranslated(['name' => $name]);

        return $deliveryTime;
    }
}
