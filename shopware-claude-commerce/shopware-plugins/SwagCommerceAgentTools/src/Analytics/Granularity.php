<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Analytics;

use Shopware\Core\Framework\DataAbstractionLayer\Search\Aggregation\Bucket\DateHistogramAggregation;

enum Granularity: string
{
    case Day = 'day';
    case Week = 'week';
    case Month = 'month';

    /**
     * @return 'day'|'week'|'month'
     */
    public function toDateHistogramInterval(): string
    {
        return match ($this) {
            self::Day => DateHistogramAggregation::PER_DAY,
            self::Week => DateHistogramAggregation::PER_WEEK,
            self::Month => DateHistogramAggregation::PER_MONTH,
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
