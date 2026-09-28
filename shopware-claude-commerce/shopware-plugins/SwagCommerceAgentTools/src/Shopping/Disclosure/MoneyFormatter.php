<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Disclosure;

/**
 * Deterministic money and quantity formatting for compliance copy.
 *
 * Intentionally not backed by ext-intl: disclosure rows are graded byte-for-byte
 * by the evals, so the output must not depend on ICU data of the host system.
 * German-style locales use "1.234,56 €", everything else "€1,234.56".
 */
class MoneyFormatter
{
    private const DECIMALS = 2;

    public function formatMoney(float $amount, string $currencySymbol, string $locale): string
    {
        if ($this->usesCommaDecimal($locale)) {
            return number_format($amount, self::DECIMALS, ',', '.') . ' ' . $currencySymbol;
        }

        return $currencySymbol . number_format($amount, self::DECIMALS, '.', ',');
    }

    /**
     * Formats a reference quantity such as 1, 0.5 or 100 without trailing zeros ("1", "0,5", "100").
     */
    public function formatQuantity(float $quantity, string $locale): string
    {
        $formatted = rtrim(rtrim(number_format($quantity, 3, '.', ''), '0'), '.');
        if ($formatted === '' || $formatted === '-0') {
            $formatted = '0';
        }

        return $this->usesCommaDecimal($locale) ? str_replace('.', ',', $formatted) : $formatted;
    }

    public function usesCommaDecimal(string $locale): bool
    {
        $language = strtolower(substr($locale, 0, 2));

        return \in_array($language, ['de', 'at', 'nl', 'fr', 'it', 'es', 'pl', 'cs', 'da', 'sv', 'fi', 'pt'], true);
    }
}
