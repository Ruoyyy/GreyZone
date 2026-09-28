<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\StagedChange;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Shopware\Core\Defaults;
use Swag\CommerceAgentTools\StagedChange\ChangeKind;
use Swag\CommerceAgentTools\StagedChange\ChangePlan;
use Swag\CommerceAgentTools\StagedChange\ChangePlanner;
use Swag\CommerceAgentTools\StagedChange\ProductSnapshot;
use Swag\CommerceAgentTools\StagedChange\StagedChangeException;

/**
 * @internal
 */
#[CoversClass(ChangePlanner::class)]
#[CoversClass(ChangePlan::class)]
#[CoversClass(ChangeKind::class)]
#[CoversClass(ProductSnapshot::class)]
class ChangePlannerTest extends TestCase
{
    private const PRODUCT_ID = '0190c1b2d3e4f5a6b7c8d9e0f1a2b3c4';
    private const OTHER_PRODUCT_ID = '0190c1b2d3e4f5a6b7c8d9e0f1a2b3c5';
    private const USD_ID = 'a1b2c3d4e5f60718293a4b5c6d7e8f90';

    private ChangePlanner $planner;

    protected function setUp(): void
    {
        $this->planner = new ChangePlanner();
    }

    public function testListingUpdateProducesPayloadAndPreview(): void
    {
        $plan = $this->planner->plan(ChangeKind::ListingUpdate, [
            ['productId' => self::PRODUCT_ID, 'field' => 'description', 'value' => 'Neue Beschreibung'],
            ['productId' => self::PRODUCT_ID, 'field' => 'metaTitle', 'value' => 'Neuer Titel'],
        ], [self::PRODUCT_ID => $this->snapshot()]);

        static::assertSame('product', $plan->targetEntity);
        static::assertSame([[
            'id' => self::PRODUCT_ID,
            'description' => 'Neue Beschreibung',
            'metaTitle' => 'Neuer Titel',
        ]], $plan->payload);
        static::assertSame([
            ['target' => self::PRODUCT_ID, 'targetLabel' => 'Hocker', 'field' => 'description', 'before' => 'Alte Beschreibung', 'after' => 'Neue Beschreibung'],
            ['target' => self::PRODUCT_ID, 'targetLabel' => 'Hocker', 'field' => 'metaTitle', 'before' => null, 'after' => 'Neuer Titel'],
        ], $plan->preview);
        static::assertSame(['product:read', 'product:update'], $plan->requiredPrivileges());
    }

    #[DataProvider('forbiddenListingFieldProvider')]
    public function testListingUpdateRefusesFieldsOutsideTheWhitelist(string $field): void
    {
        $this->expectException(StagedChangeException::class);
        $this->expectExceptionCode(StagedChangeException::CODE_INVALID_ITEMS);
        $this->expectExceptionMessageMatches('/"field" must be one of name, description, metaTitle, metaDescription, keywords/');

        $this->planner->plan(ChangeKind::ListingUpdate, [
            ['productId' => self::PRODUCT_ID, 'field' => $field, 'value' => 'x'],
        ], [self::PRODUCT_ID => $this->snapshot()]);
    }

    /**
     * @return iterable<string, array{string}>
     */
    public static function forbiddenListingFieldProvider(): iterable
    {
        yield 'price' => ['price'];
        yield 'stock' => ['stock'];
        yield 'productNumber' => ['productNumber'];
        yield 'taxId' => ['taxId'];
        yield 'active' => ['active'];
    }

    public function testPriceUpdateComputesNetFromTaxAndKeepsOtherCurrencies(): void
    {
        $plan = $this->planner->plan(ChangeKind::PriceUpdate, [
            ['productId' => self::PRODUCT_ID, 'gross' => 119.0],
        ], [self::PRODUCT_ID => $this->snapshot()]);

        static::assertCount(1, $plan->payload);
        $prices = $plan->payload[0]['price'];
        static::assertIsArray($prices);
        static::assertCount(2, $prices);

        $byCurrency = array_column($prices, null, 'currencyId');
        static::assertSame(['currencyId' => self::USD_ID, 'gross' => 130.0, 'net' => 109.24, 'linked' => true], $byCurrency[self::USD_ID]);
        static::assertSame(['currencyId' => Defaults::CURRENCY, 'gross' => 119.0, 'net' => 100.0, 'linked' => true], $byCurrency[Defaults::CURRENCY]);

        static::assertSame([
            ['target' => self::PRODUCT_ID, 'targetLabel' => 'Hocker', 'field' => 'price.gross', 'before' => 99.0, 'after' => 119.0, 'currencyId' => Defaults::CURRENCY],
            ['target' => self::PRODUCT_ID, 'targetLabel' => 'Hocker', 'field' => 'price.net', 'before' => 83.19, 'after' => 100.0, 'currencyId' => Defaults::CURRENCY],
        ], $plan->preview);
    }

    public function testPriceUpdateAcceptsExplicitNetAndNewCurrency(): void
    {
        $chf = 'c0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5';
        $plan = $this->planner->plan(ChangeKind::PriceUpdate, [
            ['productId' => self::PRODUCT_ID, 'gross' => 120.0, 'net' => 111.11, 'currencyId' => $chf],
        ], [self::PRODUCT_ID => $this->snapshot()]);

        $byCurrency = array_column($plan->payload[0]['price'], null, 'currencyId');
        static::assertSame(['currencyId' => $chf, 'gross' => 120.0, 'net' => 111.11, 'linked' => true], $byCurrency[$chf]);
        static::assertNull($plan->preview[0]['before']);
    }

    public function testPriceUpdateRejectsNegativeAndNonNumericGross(): void
    {
        try {
            $this->planner->plan(ChangeKind::PriceUpdate, [['productId' => self::PRODUCT_ID, 'gross' => -1]], [self::PRODUCT_ID => $this->snapshot()]);
            static::fail('negative gross accepted');
        } catch (StagedChangeException $e) {
            static::assertStringContainsString('must not be negative', $e->getMessage());
        }

        try {
            $this->planner->plan(ChangeKind::PriceUpdate, [['productId' => self::PRODUCT_ID, 'gross' => '19,99']], [self::PRODUCT_ID => $this->snapshot()]);
            static::fail('string gross accepted');
        } catch (StagedChangeException $e) {
            static::assertStringContainsString('"gross" must be a number', $e->getMessage());
        }
    }

    public function testInventoryRestockAddsToCurrentStock(): void
    {
        $plan = $this->planner->plan(ChangeKind::InventoryAction, [
            ['productId' => self::PRODUCT_ID, 'action' => 'restock', 'quantity' => 25],
        ], [self::PRODUCT_ID => $this->snapshot(stock: 3)]);

        static::assertSame([['id' => self::PRODUCT_ID, 'stock' => 28]], $plan->payload);
        static::assertSame(3, $plan->preview[0]['before']);
        static::assertSame(28, $plan->preview[0]['after']);
    }

    public function testInventoryPauseAndActivateToggleActive(): void
    {
        $pause = $this->planner->plan(ChangeKind::InventoryAction, [
            ['productId' => self::PRODUCT_ID, 'action' => 'pause'],
        ], [self::PRODUCT_ID => $this->snapshot(active: true)]);
        static::assertSame([['id' => self::PRODUCT_ID, 'active' => false]], $pause->payload);

        $activate = $this->planner->plan(ChangeKind::InventoryAction, [
            ['productId' => self::PRODUCT_ID, 'action' => 'activate'],
        ], [self::PRODUCT_ID => $this->snapshot(active: false)]);
        static::assertSame([['id' => self::PRODUCT_ID, 'active' => true]], $activate->payload);
        static::assertFalse($activate->preview[0]['before']);
        static::assertTrue($activate->preview[0]['after']);
    }

    public function testRestockRequiresPositiveIntegerQuantity(): void
    {
        $this->expectException(StagedChangeException::class);
        $this->expectExceptionMessageMatches('/"quantity" must be a positive integer/');

        $this->planner->plan(ChangeKind::InventoryAction, [
            ['productId' => self::PRODUCT_ID, 'action' => 'restock', 'quantity' => 0],
        ], [self::PRODUCT_ID => $this->snapshot()]);
    }

    public function testMultipleProductsProduceOnePayloadRowEach(): void
    {
        $plan = $this->planner->plan(ChangeKind::InventoryAction, [
            ['productId' => self::PRODUCT_ID, 'action' => 'pause'],
            ['productId' => self::OTHER_PRODUCT_ID, 'action' => 'pause'],
        ], [
            self::PRODUCT_ID => $this->snapshot(),
            self::OTHER_PRODUCT_ID => $this->snapshot(id: self::OTHER_PRODUCT_ID),
        ]);

        static::assertSame([self::PRODUCT_ID, self::OTHER_PRODUCT_ID], array_column($plan->payload, 'id'));
        static::assertCount(2, $plan->preview);
    }

    public function testUnsupportedKindsAreRefused(): void
    {
        foreach ([ChangeKind::Promotion, ChangeKind::Campaign] as $kind) {
            try {
                $this->planner->plan($kind, [['productId' => self::PRODUCT_ID]], [self::PRODUCT_ID => $this->snapshot()]);
                static::fail($kind->value . ' accepted');
            } catch (StagedChangeException $e) {
                static::assertSame(StagedChangeException::CODE_KIND_NOT_SUPPORTED, $e->getCode());
            }
        }
        static::assertSame(['listing_update', 'price_update', 'inventory_action'], ChangeKind::supportedValues());
    }

    public function testMissingSnapshotIsReported(): void
    {
        $this->expectException(StagedChangeException::class);
        $this->expectExceptionCode(StagedChangeException::CODE_PRODUCTS_MISSING);

        $this->planner->plan(ChangeKind::InventoryAction, [
            ['productId' => self::OTHER_PRODUCT_ID, 'action' => 'pause'],
        ], [self::PRODUCT_ID => $this->snapshot()]);
    }

    public function testEmptyItemsAndInvalidProductIdsAreRefused(): void
    {
        try {
            $this->planner->plan(ChangeKind::InventoryAction, [], []);
            static::fail('empty items accepted');
        } catch (StagedChangeException $e) {
            static::assertStringContainsString('at least one item', $e->getMessage());
        }

        try {
            $this->planner->referencedProductIds([['productId' => 'not-a-uuid']]);
            static::fail('invalid uuid accepted');
        } catch (StagedChangeException $e) {
            static::assertStringContainsString('"productId" must be a product UUID', $e->getMessage());
        }

        static::assertSame([self::PRODUCT_ID], $this->planner->referencedProductIds([
            ['productId' => strtoupper(self::PRODUCT_ID)],
            ['productId' => self::PRODUCT_ID],
        ]));
    }

    private function snapshot(string $id = self::PRODUCT_ID, int $stock = 10, bool $active = true): ProductSnapshot
    {
        return new ProductSnapshot(
            id: $id,
            productNumber: 'SW-1001',
            listingFields: ['name' => 'Hocker', 'description' => 'Alte Beschreibung', 'metaTitle' => null, 'metaDescription' => null, 'keywords' => null],
            stock: $stock,
            active: $active,
            taxRate: 19.0,
            prices: [
                Defaults::CURRENCY => ['gross' => 99.0, 'net' => 83.19, 'linked' => true],
                self::USD_ID => ['gross' => 130.0, 'net' => 109.24, 'linked' => true],
            ],
        );
    }
}
