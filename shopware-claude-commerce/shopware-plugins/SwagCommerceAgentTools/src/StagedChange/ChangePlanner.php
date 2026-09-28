<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

use Shopware\Core\Defaults;
use Shopware\Core\Framework\Uuid\Uuid;

/**
 * Turns the agent's change items into a DAL payload and a before/after preview.
 *
 * Pure: it works on ProductSnapshot fixtures and never touches the database, so
 * the field whitelist, the arithmetic and the validation errors are unit-tested.
 *
 * @phpstan-import-type PreviewRow from ChangePlan
 */
class ChangePlanner
{
    public const TARGET_PRODUCT = 'product';

    /** Listing fields the agent may edit. Everything else is refused at staging time. */
    public const LISTING_FIELDS = ['name', 'description', 'metaTitle', 'metaDescription', 'keywords'];

    public const INVENTORY_RESTOCK = 'restock';
    public const INVENTORY_PAUSE = 'pause';
    public const INVENTORY_ACTIVATE = 'activate';
    public const INVENTORY_ACTIONS = [self::INVENTORY_RESTOCK, self::INVENTORY_PAUSE, self::INVENTORY_ACTIVATE];

    private const MAX_LISTING_VALUE_LENGTH = 20000;
    private const PRICE_PRECISION = 4;

    /**
     * Extracts the product IDs a change refers to, so the caller can load snapshots first.
     *
     * @param list<array<string, mixed>> $items
     *
     * @return list<string>
     */
    public function referencedProductIds(array $items): array
    {
        $ids = [];
        foreach ($items as $index => $item) {
            $ids[] = $this->requireProductId($item, $index);
        }

        return array_values(array_unique($ids));
    }

    /**
     * @param list<array<string, mixed>> $items
     * @param array<string, ProductSnapshot> $snapshots keyed by product ID, must cover every referenced product
     *
     * @throws StagedChangeException
     */
    public function plan(ChangeKind $kind, array $items, array $snapshots): ChangePlan
    {
        if (!$kind->isSupported()) {
            throw StagedChangeException::kindNotSupported($kind);
        }
        if ($items === []) {
            throw StagedChangeException::invalidItems('at least one item is required.');
        }

        $payloadByProduct = [];
        $preview = [];

        foreach ($items as $index => $item) {
            $productId = $this->requireProductId($item, $index);
            $snapshot = $snapshots[$productId] ?? null;
            if ($snapshot === null) {
                throw StagedChangeException::productsMissing([$productId]);
            }

            [$fields, $rows] = match ($kind) {
                ChangeKind::ListingUpdate => $this->planListingUpdate($item, $index, $snapshot),
                ChangeKind::PriceUpdate => $this->planPriceUpdate($item, $index, $snapshot),
                ChangeKind::InventoryAction => $this->planInventoryAction($item, $index, $snapshot),
                default => throw StagedChangeException::kindNotSupported($kind),
            };

            $payloadByProduct[$productId] = array_merge($payloadByProduct[$productId] ?? ['id' => $productId], $fields);
            foreach ($rows as $row) {
                $preview[] = $row;
            }
        }

        return new ChangePlan($kind, self::TARGET_PRODUCT, array_values($payloadByProduct), $preview);
    }

    /**
     * @param array<string, mixed> $item
     *
     * @return array{0: array<string, mixed>, 1: list<PreviewRow>}
     */
    private function planListingUpdate(array $item, int $index, ProductSnapshot $snapshot): array
    {
        $field = $item['field'] ?? null;
        if (!\is_string($field) || !\in_array($field, self::LISTING_FIELDS, true)) {
            throw StagedChangeException::invalidItems(\sprintf(
                'item %d: "field" must be one of %s (got %s).',
                $index,
                implode(', ', self::LISTING_FIELDS),
                \is_string($field) ? '"' . $field . '"' : 'nothing',
            ));
        }

        $value = $item['value'] ?? null;
        if (!\is_string($value)) {
            throw StagedChangeException::invalidItems(\sprintf('item %d: "value" must be a string.', $index));
        }
        if (mb_strlen($value) > self::MAX_LISTING_VALUE_LENGTH) {
            throw StagedChangeException::invalidItems(\sprintf('item %d: "value" exceeds %d characters.', $index, self::MAX_LISTING_VALUE_LENGTH));
        }

        return [
            [$field => $value],
            [$this->row($snapshot, $field, $snapshot->listingFields[$field] ?? null, $value)],
        ];
    }

    /**
     * @param array<string, mixed> $item
     *
     * @return array{0: array<string, mixed>, 1: list<PreviewRow>}
     */
    private function planPriceUpdate(array $item, int $index, ProductSnapshot $snapshot): array
    {
        $gross = $item['gross'] ?? null;
        if (!\is_int($gross) && !\is_float($gross)) {
            throw StagedChangeException::invalidItems(\sprintf('item %d: "gross" must be a number.', $index));
        }
        $gross = (float) $gross;
        if ($gross < 0) {
            throw StagedChangeException::invalidItems(\sprintf('item %d: "gross" must not be negative.', $index));
        }

        $currencyId = $item['currencyId'] ?? Defaults::CURRENCY;
        $currencyId = \is_string($currencyId) ? strtolower(trim($currencyId)) : '';
        if ($currencyId === '' || !Uuid::isValid($currencyId)) {
            throw StagedChangeException::invalidItems(\sprintf('item %d: "currencyId" must be a currency UUID.', $index));
        }

        $net = $item['net'] ?? null;
        if ($net !== null && !\is_int($net) && !\is_float($net)) {
            throw StagedChangeException::invalidItems(\sprintf('item %d: "net" must be a number when given.', $index));
        }
        $net = $net === null
            ? round($gross / (1 + $snapshot->taxRate / 100), self::PRICE_PRECISION)
            : (float) $net;

        $before = $snapshot->prices[$currencyId] ?? null;
        $linked = $before['linked'] ?? true;

        $prices = [];
        foreach ($snapshot->prices as $existingCurrencyId => $existing) {
            if ($existingCurrencyId === $currencyId) {
                continue;
            }
            $prices[] = ['currencyId' => $existingCurrencyId] + $existing;
        }
        $prices[] = ['currencyId' => $currencyId, 'gross' => $gross, 'net' => $net, 'linked' => $linked];

        return [
            ['price' => $prices],
            [
                $this->row($snapshot, 'price.gross', $before['gross'] ?? null, $gross, $currencyId),
                $this->row($snapshot, 'price.net', $before['net'] ?? null, $net, $currencyId),
            ],
        ];
    }

    /**
     * @param array<string, mixed> $item
     *
     * @return array{0: array<string, mixed>, 1: list<PreviewRow>}
     */
    private function planInventoryAction(array $item, int $index, ProductSnapshot $snapshot): array
    {
        $action = $item['action'] ?? null;
        if (!\is_string($action) || !\in_array($action, self::INVENTORY_ACTIONS, true)) {
            throw StagedChangeException::invalidItems(\sprintf('item %d: "action" must be one of %s.', $index, implode(', ', self::INVENTORY_ACTIONS)));
        }

        if ($action === self::INVENTORY_RESTOCK) {
            $quantity = $item['quantity'] ?? null;
            if (!\is_int($quantity) || $quantity <= 0) {
                throw StagedChangeException::invalidItems(\sprintf('item %d: "quantity" must be a positive integer for restock.', $index));
            }
            $after = $snapshot->stock + $quantity;

            return [['stock' => $after], [$this->row($snapshot, 'stock', $snapshot->stock, $after)]];
        }

        $after = $action === self::INVENTORY_ACTIVATE;

        return [['active' => $after], [$this->row($snapshot, 'active', $snapshot->active, $after)]];
    }

    /**
     * @param array<string, mixed> $item
     */
    private function requireProductId(array $item, int $index): string
    {
        $productId = $item['productId'] ?? null;
        $normalized = \is_string($productId) ? strtolower(trim($productId)) : '';
        if ($normalized === '' || !Uuid::isValid($normalized)) {
            throw StagedChangeException::invalidItems(\sprintf('item %d: "productId" must be a product UUID.', $index));
        }

        return $normalized;
    }

    /**
     * @return PreviewRow
     */
    private function row(ProductSnapshot $snapshot, string $field, mixed $before, mixed $after, ?string $currencyId = null): array
    {
        $row = [
            'target' => $snapshot->id,
            'targetLabel' => $snapshot->label(),
            'field' => $field,
            'before' => $before,
            'after' => $after,
        ];
        if ($currencyId !== null) {
            $row['currencyId'] = $currencyId;
        }

        return $row;
    }
}
