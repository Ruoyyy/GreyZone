<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Disclosure;

use Symfony\Contracts\Translation\TranslatorInterface;

/**
 * Builds the server-authored compliance rows (PAngV / price indication) for a product.
 *
 * Every string the shopper may see comes from the plugin snippets, never from
 * free text: the model only relays the rows byte for byte.
 */
class DisclosureFormatter
{
    public const ROW_PRICE = 'price';
    public const ROW_BASE_PRICE = 'base_price';
    public const ROW_DELIVERY_TIME = 'delivery_time';
    public const ROW_TAX = 'tax';
    public const ROW_SHIPPING = 'shipping';

    private const SNIPPET_PREFIX = 'swag-commerce-agent-tools.disclosure.';

    public function __construct(
        private readonly TranslatorInterface $translator,
        private readonly MoneyFormatter $moneyFormatter,
    ) {
    }

    /**
     * @return list<array{key: string, label: string, value: string, text: string, url: string|null}>
     */
    public function format(DisclosureInput $input): array
    {
        $rows = [];

        $price = $this->moneyFormatter->formatMoney($input->unitPrice, $input->currencySymbol, $input->locale);
        $rows[] = $this->row(self::ROW_PRICE, ['%price%' => $price]);

        if ($input->referencePrice !== null && $input->referenceUnit !== null && $input->referenceUnitName !== null) {
            $rows[] = $this->row(self::ROW_BASE_PRICE, [
                '%price%' => $this->moneyFormatter->formatMoney($input->referencePrice, $input->currencySymbol, $input->locale),
                '%quantity%' => $this->moneyFormatter->formatQuantity($input->referenceUnit, $input->locale),
                '%unit%' => $input->referenceUnitName,
            ]);
        }

        $deliveryText = $this->deliveryTimeText($input);
        if ($deliveryText !== null) {
            $rows[] = $this->row(self::ROW_DELIVERY_TIME, ['%deliveryTime%' => $deliveryText]);
        }

        $rows[] = $this->row(self::ROW_TAX, [], $this->taxRowKey($input->taxState));

        if ($input->shippingFree) {
            $rows[] = $this->row(self::ROW_SHIPPING, [], 'shippingFree');
        } else {
            $rows[] = $this->row(self::ROW_SHIPPING, [], 'shipping', $input->shippingInfoUrl);
        }

        return $rows;
    }

    private function deliveryTimeText(DisclosureInput $input): ?string
    {
        if ($input->deliveryTimeName !== null && trim($input->deliveryTimeName) !== '') {
            return trim($input->deliveryTimeName);
        }

        if ($input->deliveryMin === null || $input->deliveryMax === null || $input->deliveryUnit === null) {
            return null;
        }

        $unit = $this->translator->trans(self::SNIPPET_PREFIX . 'deliveryUnit.' . $input->deliveryUnit);
        if ($input->deliveryMin === $input->deliveryMax) {
            return \sprintf('%d %s', $input->deliveryMin, $unit);
        }

        return \sprintf('%d-%d %s', $input->deliveryMin, $input->deliveryMax, $unit);
    }

    private function taxRowKey(string $taxState): string
    {
        return match ($taxState) {
            DisclosureInput::TAX_STATE_NET => 'taxNet',
            DisclosureInput::TAX_STATE_FREE => 'taxFree',
            default => 'taxGross',
        };
    }

    /**
     * @param array<string, string> $parameters
     *
     * @return array{key: string, label: string, value: string, text: string, url: string|null}
     */
    private function row(string $key, array $parameters, ?string $snippetKey = null, ?string $url = null): array
    {
        $snippet = self::SNIPPET_PREFIX . ($snippetKey ?? $this->snippetKeyForRow($key));

        $label = $this->translator->trans($snippet . '.label');
        $value = $this->translator->trans($snippet . '.value', $parameters);
        $text = $this->translator->trans($snippet . '.text', $parameters + ['%label%' => $label, '%value%' => $value]);

        return [
            'key' => $key,
            'label' => $label,
            'value' => $value,
            'text' => $text,
            'url' => $url,
        ];
    }

    private function snippetKeyForRow(string $key): string
    {
        return match ($key) {
            self::ROW_PRICE => 'price',
            self::ROW_BASE_PRICE => 'basePrice',
            self::ROW_DELIVERY_TIME => 'deliveryTime',
            default => $key,
        };
    }
}
