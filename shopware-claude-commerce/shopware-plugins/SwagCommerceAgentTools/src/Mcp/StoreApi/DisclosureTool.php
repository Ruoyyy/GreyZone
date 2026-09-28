<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\StoreApi;

use Mcp\Capability\Attribute\McpTool;
use Shopware\Core\Content\Product\SalesChannel\Detail\AbstractProductDetailRoute;
use Shopware\Core\Content\Product\SalesChannel\SalesChannelProductEntity;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Criteria;
use Shopware\Core\Framework\Mcp\Attribute\McpToolGroup;
use Shopware\Core\Framework\Mcp\Context\StoreApiMcpContextProvider;
use Shopware\Core\Framework\Mcp\Tool\McpToolResponse;
use Shopware\Core\Framework\Uuid\Uuid;
use Shopware\Core\System\Locale\LanguageLocaleCodeProvider;
use Shopware\Core\System\SalesChannel\SalesChannelContext;
use Shopware\Core\System\SystemConfig\SystemConfigService;
use Swag\CommerceAgentTools\Shopping\Disclosure\DisclosureFormatter;
use Swag\CommerceAgentTools\Shopping\Disclosure\DisclosureInput;
use Symfony\Component\HttpFoundation\Request;

/**
 * @experimental stableVersion:v6.8.0 feature:MCP_SERVER
 */
#[McpTool(name: 'shopping-disclosure', title: 'Product Disclosure', description: 'Legally required price and delivery statements for one product, authored by the shop: unit price, base price per reference unit (Grundpreis, e.g. "2,50 € / 1 l"), delivery time, VAT note and shipping-cost note with the shop\'s shipping page. Relay the returned "text" values verbatim whenever a price or delivery time is shown; never paraphrase them. Needs a product UUID from a previous search.')]
#[McpToolGroup('agent-shopping')]
class DisclosureTool extends McpToolResponse
{
    public const CONFIG_SHIPPING_INFO_URL = 'SwagCommerceAgentTools.config.shippingInfoUrl';

    /**
     * @internal
     */
    public function __construct(
        private readonly StoreApiMcpContextProvider $contextProvider,
        private readonly AbstractProductDetailRoute $productDetailRoute,
        private readonly DisclosureFormatter $formatter,
        private readonly LanguageLocaleCodeProvider $localeProvider,
        private readonly SystemConfigService $systemConfig,
    ) {
    }

    public function __invoke(string $productId): string
    {
        $context = $this->contextProvider->getSalesChannelContext();
        if ($context === null) {
            return $this->error('No Store API sales-channel context is available for this MCP request.');
        }

        $productId = strtolower(trim($productId));
        if (!Uuid::isValid($productId)) {
            return $this->error('"productId" must be a product UUID (32 hex characters) returned by a previous product search.');
        }

        $criteria = new Criteria();
        $criteria->addAssociation('deliveryTime');
        $criteria->addAssociation('unit');

        try {
            $product = $this->productDetailRoute->load($productId, new Request(), $context, $criteria)->getProduct();
        } catch (\Throwable $e) {
            return $this->error(\sprintf('Product "%s" is not available in this sales channel: %s', $productId, $e->getMessage()));
        }

        $input = $this->buildInput($product, $context);
        $rows = $this->formatter->format($input);

        return $this->success([
            'productId' => $product->getId(),
            'rows' => $rows,
        ], [
            'locale' => $input->locale,
            'currency' => $context->getCurrency()->getIsoCode(),
            'taxState' => $input->taxState,
        ]);
    }

    private function buildInput(SalesChannelProductEntity $product, SalesChannelContext $context): DisclosureInput
    {
        $calculatedPrice = $product->getCalculatedPrice();
        $referencePrice = $calculatedPrice->getReferencePrice();
        $deliveryTime = $product->getDeliveryTime();
        $deliveryTimeName = $deliveryTime?->getTranslation('name');

        $shippingInfoUrl = trim($this->systemConfig->getString(self::CONFIG_SHIPPING_INFO_URL, $context->getSalesChannelId()));

        return new DisclosureInput(
            productId: $product->getId(),
            currencySymbol: $context->getCurrency()->getSymbol(),
            locale: $this->localeProvider->getLocaleForLanguageId($context->getLanguageId()),
            unitPrice: $calculatedPrice->getUnitPrice(),
            referencePrice: $referencePrice?->getPrice(),
            referenceUnit: $referencePrice?->getReferenceUnit(),
            referenceUnitName: $referencePrice?->getUnitName(),
            deliveryTimeName: \is_string($deliveryTimeName) ? $deliveryTimeName : null,
            deliveryMin: $deliveryTime?->getMin(),
            deliveryMax: $deliveryTime?->getMax(),
            deliveryUnit: $deliveryTime?->getUnit(),
            taxState: $context->getTaxState(),
            shippingFree: $product->getShippingFree() === true,
            shippingInfoUrl: $shippingInfoUrl !== '' ? $shippingInfoUrl : null,
        );
    }
}
