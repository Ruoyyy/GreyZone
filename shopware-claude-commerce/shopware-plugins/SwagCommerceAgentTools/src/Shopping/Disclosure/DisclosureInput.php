<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Disclosure;

/**
 * Everything the formatter needs, already detached from DAL entities so the
 * formatter can be unit-tested without a sales-channel context.
 */
final class DisclosureInput
{
    public const TAX_STATE_GROSS = 'gross';
    public const TAX_STATE_NET = 'net';
    public const TAX_STATE_FREE = 'tax-free';

    /**
     * @param float|null $referencePrice price per reference unit (e.g. 2.50 per 1 l)
     * @param float|null $referenceUnit reference quantity (e.g. 1.0 or 100.0)
     * @param string|null $referenceUnitName unit label already translated (e.g. "l", "kg")
     * @param string|null $deliveryTimeName translated delivery time label (e.g. "2-5 Tage")
     * @param string $taxState one of the TAX_STATE_* constants
     */
    public function __construct(
        public readonly string $productId,
        public readonly string $currencySymbol,
        public readonly string $locale,
        public readonly float $unitPrice,
        public readonly ?float $referencePrice,
        public readonly ?float $referenceUnit,
        public readonly ?string $referenceUnitName,
        public readonly ?string $deliveryTimeName,
        public readonly ?int $deliveryMin,
        public readonly ?int $deliveryMax,
        public readonly ?string $deliveryUnit,
        public readonly string $taxState,
        public readonly bool $shippingFree,
        public readonly ?string $shippingInfoUrl,
    ) {
    }
}
