<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

/**
 * The current state of a product as far as staged changes care about it.
 * Detached from the DAL so the planner can be unit-tested with fixtures.
 */
final class ProductSnapshot
{
    /**
     * @param array<string, string|null> $listingFields translated listing fields keyed by field name
     * @param array<string, array{gross: float, net: float, linked: bool}> $prices keyed by currency ID
     */
    public function __construct(
        public readonly string $id,
        public readonly ?string $productNumber,
        public readonly array $listingFields,
        public readonly int $stock,
        public readonly bool $active,
        public readonly float $taxRate,
        public readonly array $prices,
    ) {
    }

    public function label(): string
    {
        $name = $this->listingFields['name'] ?? null;
        if (\is_string($name) && $name !== '') {
            return $name;
        }

        return $this->productNumber ?? $this->id;
    }
}
