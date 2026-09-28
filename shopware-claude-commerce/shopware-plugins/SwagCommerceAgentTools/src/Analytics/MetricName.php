<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Analytics;

/**
 * Metrics of the Blueprint's `query_metrics`. Traffic and conversion are part of
 * the contract but Shopware core does not measure them; the tools return null
 * series with a note so the agent can say so instead of inventing numbers.
 */
enum MetricName: string
{
    case Sales = 'sales';
    case Orders = 'orders';
    case AverageOrderValue = 'aov';
    case Units = 'units';
    case Traffic = 'traffic';
    case Conversion = 'conversion';

    public function isMeasured(): bool
    {
        return match ($this) {
            self::Traffic, self::Conversion => false,
            default => true,
        };
    }

    public function unavailableNote(): ?string
    {
        return match ($this) {
            self::Traffic => 'Shopware core does not record storefront traffic. Connect a web-analytics source (e.g. Shopware Analytics) to report visits.',
            self::Conversion => 'Conversion rate needs a traffic denominator, which Shopware core does not record. Orders are available via the "orders" metric.',
            default => null,
        };
    }

    /**
     * @return list<string>
     */
    public static function values(): array
    {
        return array_column(self::cases(), 'value');
    }
}
