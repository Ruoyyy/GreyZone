<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Shopping\Disclosure;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Swag\CommerceAgentTools\Shopping\Disclosure\DisclosureFormatter;
use Swag\CommerceAgentTools\Shopping\Disclosure\DisclosureInput;
use Swag\CommerceAgentTools\Shopping\Disclosure\MoneyFormatter;
use Swag\CommerceAgentTools\Tests\Unit\Support\SnippetTranslator;

/**
 * @internal
 */
#[CoversClass(DisclosureFormatter::class)]
#[CoversClass(MoneyFormatter::class)]
#[CoversClass(DisclosureInput::class)]
class DisclosureFormatterTest extends TestCase
{
    public function testGermanRowsAreByteExact(): void
    {
        $rows = $this->formatter('de-DE')->format($this->input(
            unitPrice: 2.5,
            referencePrice: 2.5,
            referenceUnit: 1.0,
            referenceUnitName: 'l',
            deliveryTimeName: '2-5 Tage',
        ));

        $byKey = array_column($rows, null, 'key');

        static::assertSame(['price', 'base_price', 'delivery_time', 'tax', 'shipping'], array_keys($byKey));
        static::assertSame('2,50 €', $byKey['price']['text']);
        static::assertSame('Grundpreis: 2,50 € / 1 l', $byKey['base_price']['text']);
        static::assertSame('2,50 € / 1 l', $byKey['base_price']['value']);
        static::assertSame('Lieferzeit: 2-5 Tage', $byKey['delivery_time']['text']);
        static::assertSame('inkl. MwSt.', $byKey['tax']['value']);
        static::assertSame('Alle Preise inkl. MwSt.', $byKey['tax']['text']);
        static::assertSame('Alle Preise zzgl. Versandkosten', $byKey['shipping']['text']);
        static::assertSame('https://shop.example/versand', $byKey['shipping']['url']);
    }

    public function testEnglishRowsUseEnglishNumberFormat(): void
    {
        $rows = $this->formatter('en-GB')->format($this->input(
            unitPrice: 1234.5,
            referencePrice: 12.345,
            referenceUnit: 100.0,
            referenceUnitName: 'g',
            deliveryTimeName: '2-5 days',
            locale: 'en-GB',
        ));

        $byKey = array_column($rows, null, 'key');

        static::assertSame('€1,234.50', $byKey['price']['text']);
        static::assertSame('Base price: €12.35 / 100 g', $byKey['base_price']['text']);
        static::assertSame('All prices incl. VAT', $byKey['tax']['text']);
    }

    public function testBasePriceRowIsOmittedWithoutReferencePrice(): void
    {
        $rows = $this->formatter('de-DE')->format($this->input(unitPrice: 9.99));

        static::assertNotContains('base_price', array_column($rows, 'key'));
    }

    public function testDeliveryTimeFallsBackToRangeAndUnit(): void
    {
        $rows = $this->formatter('de-DE')->format($this->input(
            unitPrice: 9.99,
            deliveryTimeName: null,
            deliveryMin: 1,
            deliveryMax: 3,
            deliveryUnit: 'day',
        ));
        $byKey = array_column($rows, null, 'key');

        static::assertSame('Lieferzeit: 1-3 Werktage', $byKey['delivery_time']['text']);
    }

    public function testSameMinMaxDeliveryTimeIsCollapsed(): void
    {
        $rows = $this->formatter('de-DE')->format($this->input(
            unitPrice: 9.99,
            deliveryTimeName: '',
            deliveryMin: 2,
            deliveryMax: 2,
            deliveryUnit: 'week',
        ));
        $byKey = array_column($rows, null, 'key');

        static::assertSame('Lieferzeit: 2 Wochen', $byKey['delivery_time']['text']);
    }

    public function testDeliveryRowIsOmittedWithoutAnyDeliveryData(): void
    {
        $rows = $this->formatter('de-DE')->format($this->input(unitPrice: 9.99, deliveryTimeName: null));

        static::assertNotContains('delivery_time', array_column($rows, 'key'));
    }

    #[DataProvider('taxStateProvider')]
    public function testTaxRowFollowsTaxState(string $taxState, string $expectedValue): void
    {
        $rows = $this->formatter('de-DE')->format($this->input(unitPrice: 9.99, taxState: $taxState));
        $byKey = array_column($rows, null, 'key');

        static::assertSame($expectedValue, $byKey['tax']['value']);
    }

    /**
     * @return iterable<string, array{string, string}>
     */
    public static function taxStateProvider(): iterable
    {
        yield 'gross' => [DisclosureInput::TAX_STATE_GROSS, 'inkl. MwSt.'];
        yield 'net' => [DisclosureInput::TAX_STATE_NET, 'zzgl. MwSt.'];
        yield 'tax-free' => [DisclosureInput::TAX_STATE_FREE, 'ohne MwSt.'];
    }

    public function testShippingFreeProductGetsFreeShippingRowWithoutLink(): void
    {
        $rows = $this->formatter('de-DE')->format($this->input(unitPrice: 9.99, shippingFree: true));
        $byKey = array_column($rows, null, 'key');

        static::assertSame('Versandkostenfrei', $byKey['shipping']['text']);
        static::assertNull($byKey['shipping']['url']);
    }

    public function testShippingRowHasNoUrlWhenNotConfigured(): void
    {
        $rows = $this->formatter('de-DE')->format($this->input(unitPrice: 9.99, shippingInfoUrl: null));
        $byKey = array_column($rows, null, 'key');

        static::assertNull($byKey['shipping']['url']);
    }

    public function testEverySnippetUsedByTheFormatterExistsInBothLocales(): void
    {
        foreach (['de-DE', 'en-GB'] as $locale) {
            $translator = new SnippetTranslator($locale);
            foreach (['price', 'basePrice', 'deliveryTime', 'taxGross', 'taxNet', 'taxFree', 'shipping', 'shippingFree'] as $row) {
                foreach (['label', 'value', 'text'] as $part) {
                    static::assertTrue($translator->has(\sprintf('swag-commerce-agent-tools.disclosure.%s.%s', $row, $part)), \sprintf('%s: %s.%s missing', $locale, $row, $part));
                }
            }
            foreach (['hour', 'day', 'week', 'month', 'year'] as $unit) {
                static::assertTrue($translator->has('swag-commerce-agent-tools.disclosure.deliveryUnit.' . $unit), \sprintf('%s: deliveryUnit.%s missing', $locale, $unit));
            }
        }
    }

    #[DataProvider('quantityProvider')]
    public function testQuantityFormatting(float $quantity, string $locale, string $expected): void
    {
        static::assertSame($expected, (new MoneyFormatter())->formatQuantity($quantity, $locale));
    }

    /**
     * @return iterable<string, array{float, string, string}>
     */
    public static function quantityProvider(): iterable
    {
        yield 'one de' => [1.0, 'de-DE', '1'];
        yield 'half de' => [0.5, 'de-DE', '0,5'];
        yield 'hundred en' => [100.0, 'en-GB', '100'];
        yield 'quarter en' => [0.25, 'en-GB', '0.25'];
        yield 'zero' => [0.0, 'de-DE', '0'];
    }

    private function formatter(string $locale): DisclosureFormatter
    {
        return new DisclosureFormatter(new SnippetTranslator($locale), new MoneyFormatter());
    }

    private function input(
        float $unitPrice,
        ?float $referencePrice = null,
        ?float $referenceUnit = null,
        ?string $referenceUnitName = null,
        ?string $deliveryTimeName = '2-5 Tage',
        ?int $deliveryMin = null,
        ?int $deliveryMax = null,
        ?string $deliveryUnit = null,
        string $taxState = DisclosureInput::TAX_STATE_GROSS,
        bool $shippingFree = false,
        ?string $shippingInfoUrl = 'https://shop.example/versand',
        string $locale = 'de-DE',
    ): DisclosureInput {
        return new DisclosureInput(
            productId: '0190c1b2d3e4f5a6b7c8d9e0f1a2b3c4',
            currencySymbol: '€',
            locale: $locale,
            unitPrice: $unitPrice,
            referencePrice: $referencePrice,
            referenceUnit: $referenceUnit,
            referenceUnitName: $referenceUnitName,
            deliveryTimeName: $deliveryTimeName,
            deliveryMin: $deliveryMin,
            deliveryMax: $deliveryMax,
            deliveryUnit: $deliveryUnit,
            taxState: $taxState,
            shippingFree: $shippingFree,
            shippingInfoUrl: $shippingInfoUrl,
        );
    }
}
