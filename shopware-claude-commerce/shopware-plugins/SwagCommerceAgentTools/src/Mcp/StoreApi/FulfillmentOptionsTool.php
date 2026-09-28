<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\StoreApi;

use Mcp\Capability\Attribute\McpTool;
use Shopware\Core\Checkout\Cart\Cart;
use Shopware\Core\Checkout\Cart\SalesChannel\CartService;
use Shopware\Core\Checkout\Shipping\SalesChannel\AbstractShippingMethodRoute;
use Shopware\Core\Checkout\Shipping\ShippingMethodEntity;
use Shopware\Core\Content\Product\SalesChannel\AbstractProductListRoute;
use Shopware\Core\Content\Product\SalesChannel\SalesChannelProductEntity;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Criteria;
use Shopware\Core\Framework\Mcp\Attribute\McpToolGroup;
use Shopware\Core\Framework\Mcp\Context\StoreApiMcpContextProvider;
use Shopware\Core\Framework\Mcp\Tool\McpToolResponse;
use Shopware\Core\System\SalesChannel\SalesChannelContext;
use Swag\CommerceAgentTools\Mcp\Support\IdListParser;
use Swag\CommerceAgentTools\Shopping\Fulfillment\DeliveryTimeFormatter;
use Swag\CommerceAgentTools\Shopping\Fulfillment\ShippingFeeResolver;
use Symfony\Component\HttpFoundation\Request;

/**
 * @experimental stableVersion:v6.8.0 feature:MCP_SERVER
 */
#[McpTool(name: 'shopping-fulfillment-options', title: 'Fulfillment Options', description: 'How and when the given products can be delivered: every shipping method available in this sales channel with its fee and the expected delivery time per product. Use it for "how long does shipping take", "what does delivery cost" and "which carriers are available". Read-only; changing the selected shipping method is done in the checkout, not here. Pass productIds as a JSON array or comma-separated UUIDs from a previous search.')]
#[McpToolGroup('agent-shopping')]
class FulfillmentOptionsTool extends McpToolResponse
{
    public const METHOD_SHIPPING = 'shipping';

    private const MAX_PRODUCTS = 20;

    /**
     * @internal
     */
    public function __construct(
        private readonly StoreApiMcpContextProvider $contextProvider,
        private readonly AbstractShippingMethodRoute $shippingMethodRoute,
        private readonly AbstractProductListRoute $productListRoute,
        private readonly CartService $cartService,
        private readonly ShippingFeeResolver $feeResolver,
        private readonly DeliveryTimeFormatter $deliveryTimeFormatter,
    ) {
    }

    public function __invoke(string $productIds): string
    {
        $context = $this->contextProvider->getSalesChannelContext();
        if ($context === null) {
            return $this->error('No Store API sales-channel context is available for this MCP request.');
        }

        $ids = IdListParser::parse($productIds, 'productIds', self::MAX_PRODUCTS);
        if (\is_string($ids)) {
            return $this->error($ids);
        }

        try {
            $products = $this->loadProducts($ids, $context);
            $methods = $this->loadShippingMethods($context);
            $cart = $this->loadCart($context);
        } catch (\Throwable $e) {
            return $this->error('Fulfillment options could not be loaded: ' . $e->getMessage());
        }

        $unknownIds = array_values(array_diff($ids, array_keys($products)));
        if ($products === []) {
            return $this->error('None of the given products is available in this sales channel. Use IDs from a previous product search.');
        }

        $options = [];
        foreach ($methods as $method) {
            $options[] = $this->buildOption($method, $products, $context, $cart);
        }

        return $this->success([
            'options' => $options,
            'products' => array_values(array_map(fn (SalesChannelProductEntity $product): array => [
                'productId' => $product->getId(),
                'name' => $product->getTranslation('name'),
                'shippingFree' => $product->getShippingFree() === true,
                'deliveryTime' => $this->deliveryTimeFormatter->format($product->getDeliveryTime()),
            ], $products)),
        ], [
            'salesChannelId' => $context->getSalesChannelId(),
            'selectedShippingMethodId' => $context->getShippingMethod()->getId(),
            'unknownProductIds' => $unknownIds,
        ]);
    }

    /**
     * @param list<string> $ids
     *
     * @return array<string, SalesChannelProductEntity> keyed by product ID
     */
    private function loadProducts(array $ids, SalesChannelContext $context): array
    {
        $criteria = new Criteria($ids);
        $criteria->addAssociation('deliveryTime');

        $products = [];
        foreach ($this->productListRoute->load($criteria, $context)->getProducts() as $product) {
            if ($product instanceof SalesChannelProductEntity) {
                $products[$product->getId()] = $product;
            }
        }

        return $products;
    }

    /**
     * @return list<ShippingMethodEntity>
     */
    private function loadShippingMethods(SalesChannelContext $context): array
    {
        $criteria = new Criteria();
        $criteria->addAssociation('prices');
        $criteria->addAssociation('deliveryTime');

        $request = new Request(['onlyAvailable' => 1]);
        $collection = $this->shippingMethodRoute->load($request, $context, $criteria)->getShippingMethods();

        return array_values($collection->getElements());
    }

    private function loadCart(SalesChannelContext $context): ?Cart
    {
        try {
            $cart = $this->cartService->getCart($context->getToken(), $context);
        } catch (\Throwable) {
            return null;
        }

        return $cart->getLineItems()->count() > 0 ? $cart : null;
    }

    /**
     * @param array<string, SalesChannelProductEntity> $products
     *
     * @return array<string, mixed>
     */
    private function buildOption(ShippingMethodEntity $method, array $products, SalesChannelContext $context, ?Cart $cart): array
    {
        $methodEta = $this->deliveryTimeFormatter->format($method->getDeliveryTime());
        $eta = null;
        $perProduct = [];

        foreach ($products as $product) {
            $productEta = $this->deliveryTimeFormatter->format($product->getDeliveryTime()) ?? $methodEta;
            $eta = $this->deliveryTimeFormatter->widen($eta, $productEta);
            $perProduct[] = [
                'productId' => $product->getId(),
                'eta' => $productEta,
            ];
        }

        $fee = $this->feeResolver->resolve($method, $context, $cart);
        $allShippingFree = array_reduce(
            $products,
            static fn (bool $carry, SalesChannelProductEntity $product): bool => $carry && $product->getShippingFree() === true,
            true,
        );

        return [
            'method' => self::METHOD_SHIPPING,
            'shippingMethodId' => $method->getId(),
            'name' => $method->getTranslation('name'),
            'description' => $method->getTranslation('description'),
            'selected' => $method->getId() === $context->getShippingMethod()->getId(),
            // eta: when the parcel can be with the shopper (widest product delivery time,
            // falling back to the method's); shippingTime: the carrier's own transport time,
            // so "Express 1-2 Tage" stays visible next to a product that needs 2-4 Tage to ship.
            'eta' => $eta,
            'shippingTime' => $methodEta,
            'fee' => $allShippingFree
                ? ['amount' => 0.0, 'currency' => $context->getCurrency()->getIsoCode(), 'estimated' => false]
                : $fee?->toArray(),
            'location' => null,
            'products' => $perProduct,
        ];
    }
}
