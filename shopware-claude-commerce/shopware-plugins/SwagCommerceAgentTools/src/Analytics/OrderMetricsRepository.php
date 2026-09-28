<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Analytics;

use Shopware\Core\Checkout\Cart\LineItem\LineItem;
use Shopware\Core\Checkout\Order\OrderStates;
use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\DataAbstractionLayer\DefinitionInstanceRegistry;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Aggregation\Bucket\DateHistogramAggregation;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Aggregation\Metric\CountAggregation;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Aggregation\Metric\SumAggregation;
use Shopware\Core\Framework\DataAbstractionLayer\Search\AggregationResult\AggregationResultCollection;
use Shopware\Core\Framework\DataAbstractionLayer\Search\AggregationResult\Bucket\DateHistogramResult;
use Shopware\Core\Framework\DataAbstractionLayer\Search\AggregationResult\Metric\CountResult;
use Shopware\Core\Framework\DataAbstractionLayer\Search\AggregationResult\Metric\SumResult;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Criteria;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Filter\EqualsFilter;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Filter\Filter;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Filter\MultiFilter;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Filter\NotFilter;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Filter\RangeFilter;

/**
 * Order aggregations behind the analytics tools. Everything is a DAL
 * aggregation with `limit 0`, so responses stay small regardless of shop size.
 * Cancelled orders are excluded everywhere.
 */
class OrderMetricsRepository
{
    public const ENTITY_ORDER = 'order';
    public const ENTITY_ORDER_LINE_ITEM = 'order_line_item';

    private const AGG_REVENUE = 'revenue';
    private const AGG_ORDERS = 'orders';
    private const AGG_UNITS = 'units';
    private const AGG_SERIES = 'series';
    private const AGG_SERIES_COUNT = 'seriesCount';

    public function __construct(
        private readonly DefinitionInstanceRegistry $registry,
    ) {
    }

    /**
     * @return array{revenue: float, orders: int, aov: float|null}
     */
    public function totals(\DateTimeImmutable $from, \DateTimeImmutable $to, ?string $salesChannelId, ?MetricsSegment $segment, Context $context): array
    {
        $criteria = $this->orderCriteria($from, $to, $salesChannelId, $segment);
        $criteria->addAggregation(new SumAggregation(self::AGG_REVENUE, 'amountTotal'));
        $criteria->addAggregation(new CountAggregation(self::AGG_ORDERS, 'id'));

        $aggregations = $this->registry->getRepository(self::ENTITY_ORDER)->aggregate($criteria, $context);

        $revenue = $this->sum($aggregations, self::AGG_REVENUE);
        $orders = $this->count($aggregations, self::AGG_ORDERS);

        return [
            'revenue' => round($revenue, 2),
            'orders' => $orders,
            'aov' => $orders > 0 ? round($revenue / $orders, 2) : null,
        ];
    }

    public function units(\DateTimeImmutable $from, \DateTimeImmutable $to, ?string $salesChannelId, ?MetricsSegment $segment, Context $context): int
    {
        $criteria = $this->lineItemCriteria($from, $to, $salesChannelId, $segment);
        $criteria->addAggregation(new SumAggregation(self::AGG_UNITS, 'quantity'));

        $aggregations = $this->registry->getRepository(self::ENTITY_ORDER_LINE_ITEM)->aggregate($criteria, $context);

        return (int) round($this->sum($aggregations, self::AGG_UNITS));
    }

    /**
     * @return list<array{date: string, value: float|int|null}>
     */
    public function series(MetricName $metric, Granularity $granularity, \DateTimeImmutable $from, \DateTimeImmutable $to, ?string $salesChannelId, ?MetricsSegment $segment, Context $context): array
    {
        $interval = $granularity->toDateHistogramInterval();
        $timezone = $from->getTimezone()->getName();

        if ($metric === MetricName::Units) {
            $criteria = $this->lineItemCriteria($from, $to, $salesChannelId, $segment);
            $criteria->addAggregation(new DateHistogramAggregation(self::AGG_SERIES, 'order.orderDateTime', $interval, null, new SumAggregation(self::AGG_UNITS, 'quantity'), null, $timezone));
            $aggregations = $this->registry->getRepository(self::ENTITY_ORDER_LINE_ITEM)->aggregate($criteria, $context);

            return $this->bucketsToSeries($aggregations->get(self::AGG_SERIES), static fn (?float $sum, int $count): int => (int) round($sum ?? 0.0));
        }

        $criteria = $this->orderCriteria($from, $to, $salesChannelId, $segment);
        $criteria->addAggregation(new DateHistogramAggregation(self::AGG_SERIES, 'orderDateTime', $interval, null, new SumAggregation(self::AGG_REVENUE, 'amountTotal'), null, $timezone));
        $criteria->addAggregation(new DateHistogramAggregation(self::AGG_SERIES_COUNT, 'orderDateTime', $interval, null, new CountAggregation(self::AGG_ORDERS, 'id'), null, $timezone));
        $aggregations = $this->registry->getRepository(self::ENTITY_ORDER)->aggregate($criteria, $context);

        $counts = [];
        $countResult = $aggregations->get(self::AGG_SERIES_COUNT);
        if ($countResult instanceof DateHistogramResult) {
            foreach ($countResult->getBuckets() as $bucket) {
                $nested = $bucket->getResult();
                $counts[(string) $bucket->getKey()] = $nested instanceof CountResult ? $nested->getCount() : $bucket->getCount();
            }
        }

        $valueOf = match ($metric) {
            MetricName::Sales => static fn (?float $sum, int $count): float => round($sum ?? 0.0, 2),
            MetricName::Orders => static fn (?float $sum, int $count): int => $count,
            MetricName::AverageOrderValue => static fn (?float $sum, int $count): ?float => $count > 0 ? round(($sum ?? 0.0) / $count, 2) : null,
            default => static fn (?float $sum, int $count): ?float => null,
        };

        return $this->bucketsToSeries($aggregations->get(self::AGG_SERIES), $valueOf, $counts);
    }

    private function orderCriteria(\DateTimeImmutable $from, \DateTimeImmutable $to, ?string $salesChannelId, ?MetricsSegment $segment): Criteria
    {
        $criteria = new Criteria();
        $criteria->setLimit(1);
        $criteria->setTotalCountMode(Criteria::TOTAL_COUNT_MODE_NONE);
        $criteria->addFilter($this->dateRange('orderDateTime', $from, $to));
        $criteria->addFilter($this->notCancelled('stateMachineState.technicalName'));

        if ($salesChannelId !== null) {
            $criteria->addFilter(new EqualsFilter('salesChannelId', $salesChannelId));
        }
        if ($segment !== null) {
            $criteria->addFilter(match ($segment->type) {
                MetricsSegment::TYPE_SALES_CHANNEL => new EqualsFilter('salesChannelId', $segment->id),
                default => new EqualsFilter('lineItems.product.categoriesRo.id', $segment->id),
            });
        }

        return $criteria;
    }

    private function lineItemCriteria(\DateTimeImmutable $from, \DateTimeImmutable $to, ?string $salesChannelId, ?MetricsSegment $segment): Criteria
    {
        $criteria = new Criteria();
        $criteria->setLimit(1);
        $criteria->setTotalCountMode(Criteria::TOTAL_COUNT_MODE_NONE);
        $criteria->addFilter(new EqualsFilter('type', LineItem::PRODUCT_LINE_ITEM_TYPE));
        $criteria->addFilter($this->dateRange('order.orderDateTime', $from, $to));
        $criteria->addFilter($this->notCancelled('order.stateMachineState.technicalName'));

        if ($salesChannelId !== null) {
            $criteria->addFilter(new EqualsFilter('order.salesChannelId', $salesChannelId));
        }
        if ($segment !== null) {
            $criteria->addFilter(match ($segment->type) {
                MetricsSegment::TYPE_SALES_CHANNEL => new EqualsFilter('order.salesChannelId', $segment->id),
                default => new EqualsFilter('product.categoriesRo.id', $segment->id),
            });
        }

        return $criteria;
    }

    private function dateRange(string $field, \DateTimeImmutable $from, \DateTimeImmutable $to): Filter
    {
        return new RangeFilter($field, [
            RangeFilter::GTE => $from->format(\DATE_ATOM),
            RangeFilter::LT => $to->format(\DATE_ATOM),
        ]);
    }

    private function notCancelled(string $field): Filter
    {
        return new NotFilter(MultiFilter::CONNECTION_OR, [
            new EqualsFilter($field, OrderStates::STATE_CANCELLED),
        ]);
    }

    private function sum(AggregationResultCollection $aggregations, string $name): float
    {
        $result = $aggregations->get($name);

        return $result instanceof SumResult ? $result->getSum() : 0.0;
    }

    private function count(AggregationResultCollection $aggregations, string $name): int
    {
        $result = $aggregations->get($name);

        return $result instanceof CountResult ? $result->getCount() : 0;
    }

    /**
     * @param callable(?float, int): (float|int|null) $valueOf
     * @param array<string, int> $counts per-bucket order counts keyed by bucket key
     *
     * @return list<array{date: string, value: float|int|null}>
     */
    private function bucketsToSeries(mixed $result, callable $valueOf, array $counts = []): array
    {
        if (!$result instanceof DateHistogramResult) {
            return [];
        }

        $series = [];
        foreach ($result->getBuckets() as $bucket) {
            $key = (string) $bucket->getKey();
            $nested = $bucket->getResult();
            $sum = $nested instanceof SumResult ? $nested->getSum() : null;
            $count = $counts[$key] ?? $bucket->getCount();

            $series[] = ['date' => $key, 'value' => $valueOf($sum, $count)];
        }

        return $series;
    }
}
