<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Analytics;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\TestCase;
use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\DataAbstractionLayer\DefinitionInstanceRegistry;
use Shopware\Core\Framework\DataAbstractionLayer\Entity;
use Shopware\Core\Framework\DataAbstractionLayer\EntityCollection;
use Shopware\Core\Framework\DataAbstractionLayer\EntityRepository;
use Shopware\Core\Framework\DataAbstractionLayer\Search\AggregationResult\AggregationResultCollection;
use Shopware\Core\Framework\DataAbstractionLayer\Search\AggregationResult\Bucket\Bucket;
use Shopware\Core\Framework\DataAbstractionLayer\Search\AggregationResult\Bucket\DateHistogramResult;
use Shopware\Core\Framework\DataAbstractionLayer\Search\AggregationResult\Metric\CountResult;
use Shopware\Core\Framework\DataAbstractionLayer\Search\AggregationResult\Metric\SumResult;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Criteria;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Filter\EqualsFilter;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Filter\NotFilter;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Filter\RangeFilter;
use Swag\CommerceAgentTools\Analytics\Granularity;
use Swag\CommerceAgentTools\Analytics\MetricName;
use Swag\CommerceAgentTools\Analytics\MetricsSegment;
use Swag\CommerceAgentTools\Analytics\OrderMetricsRepository;

/**
 * @internal
 */
#[CoversClass(OrderMetricsRepository::class)]
#[CoversClass(MetricsSegment::class)]
#[CoversClass(MetricName::class)]
#[CoversClass(Granularity::class)]
class OrderMetricsRepositoryTest extends TestCase
{
    private const SALES_CHANNEL_ID = '0190aaaa0000000000000000000000aa';
    private const CATEGORY_ID = '0190bbbb0000000000000000000000bb';

    public function testTotalsBuildFilteredCriteriaAndComputeAov(): void
    {
        $context = Context::createDefaultContext();
        $captured = null;

        $orders = $this->repository(function (Criteria $criteria) use (&$captured): AggregationResultCollection {
            $captured = $criteria;

            return new AggregationResultCollection([new SumResult('revenue', 1500.456), new CountResult('orders', 10)]);
        });

        $metrics = new OrderMetricsRepository($this->registry(['order' => $orders]));
        $totals = $metrics->totals(self::utc('2026-08-01'), self::utc('2026-09-01'), self::SALES_CHANNEL_ID, MetricsSegment::parse('category:' . self::CATEGORY_ID), $context);

        static::assertSame(['revenue' => 1500.46, 'orders' => 10, 'aov' => 150.05], $totals);

        static::assertInstanceOf(Criteria::class, $captured);
        static::assertSame(1, $captured->getLimit());
        $filters = $captured->getFilters();
        static::assertCount(4, $filters);
        static::assertInstanceOf(RangeFilter::class, $filters[0]);
        static::assertSame('orderDateTime', $filters[0]->getField());
        static::assertSame(['gte' => '2026-08-01T00:00:00+00:00', 'lt' => '2026-09-01T00:00:00+00:00'], $filters[0]->getParameters());
        static::assertInstanceOf(NotFilter::class, $filters[1]);
        static::assertInstanceOf(EqualsFilter::class, $filters[2]);
        static::assertSame('salesChannelId', $filters[2]->getField());
        static::assertInstanceOf(EqualsFilter::class, $filters[3]);
        static::assertSame('lineItems.product.categoriesRo.id', $filters[3]->getField());
        static::assertSame(self::CATEGORY_ID, $filters[3]->getValue());
    }

    public function testTotalsWithoutOrdersHaveNullAov(): void
    {
        $orders = $this->repository(static fn (): AggregationResultCollection => new AggregationResultCollection([]));
        $metrics = new OrderMetricsRepository($this->registry(['order' => $orders]));

        static::assertSame(['revenue' => 0.0, 'orders' => 0, 'aov' => null], $metrics->totals(self::utc('2026-08-01'), self::utc('2026-09-01'), null, null, Context::createDefaultContext()));
    }

    public function testUnitsUseLineItemsScopedToProductTypeAndOrderDate(): void
    {
        $captured = null;
        $lineItems = $this->repository(function (Criteria $criteria) use (&$captured): AggregationResultCollection {
            $captured = $criteria;

            return new AggregationResultCollection([new SumResult('units', 42.0)]);
        });
        $metrics = new OrderMetricsRepository($this->registry(['order_line_item' => $lineItems]));

        $units = $metrics->units(self::utc('2026-08-01'), self::utc('2026-09-01'), null, MetricsSegment::parse('sales_channel:' . self::SALES_CHANNEL_ID), Context::createDefaultContext());

        static::assertSame(42, $units);
        static::assertInstanceOf(Criteria::class, $captured);
        $fields = array_map(static fn ($filter) => $filter->getFields()[0] ?? null, $captured->getFilters());
        static::assertSame(['type', 'order.orderDateTime', 'order.stateMachineState.technicalName', 'order.salesChannelId'], $fields);
    }

    public function testSalesSeriesZipsRevenueAndCountHistograms(): void
    {
        $orders = $this->repository(static fn (): AggregationResultCollection => new AggregationResultCollection([
            new DateHistogramResult('series', [
                new Bucket('2026-08-01 00:00:00', 3, new SumResult('revenue', 300.0)),
                new Bucket('2026-08-02 00:00:00', 1, new SumResult('revenue', 99.999)),
            ]),
            new DateHistogramResult('seriesCount', [
                new Bucket('2026-08-01 00:00:00', 3, new CountResult('orders', 3)),
                new Bucket('2026-08-02 00:00:00', 1, new CountResult('orders', 1)),
            ]),
        ]));
        $metrics = new OrderMetricsRepository($this->registry(['order' => $orders]));
        $from = self::utc('2026-08-01');
        $to = self::utc('2026-08-03');
        $context = Context::createDefaultContext();

        static::assertSame([
            ['date' => '2026-08-01 00:00:00', 'value' => 300.0],
            ['date' => '2026-08-02 00:00:00', 'value' => 100.0],
        ], $metrics->series(MetricName::Sales, Granularity::Day, $from, $to, null, null, $context));

        static::assertSame([
            ['date' => '2026-08-01 00:00:00', 'value' => 3],
            ['date' => '2026-08-02 00:00:00', 'value' => 1],
        ], $metrics->series(MetricName::Orders, Granularity::Day, $from, $to, null, null, $context));

        static::assertSame([
            ['date' => '2026-08-01 00:00:00', 'value' => 100.0],
            ['date' => '2026-08-02 00:00:00', 'value' => 100.0],
        ], $metrics->series(MetricName::AverageOrderValue, Granularity::Week, $from, $to, null, null, $context));
    }

    public function testUnitsSeriesUsesLineItemRepository(): void
    {
        $lineItems = $this->repository(static fn (): AggregationResultCollection => new AggregationResultCollection([
            new DateHistogramResult('series', [new Bucket('2026-08-01 00:00:00', 2, new SumResult('units', 7.0))]),
        ]));
        $metrics = new OrderMetricsRepository($this->registry(['order_line_item' => $lineItems]));

        static::assertSame(
            [['date' => '2026-08-01 00:00:00', 'value' => 7]],
            $metrics->series(MetricName::Units, Granularity::Month, self::utc('2026-08-01'), self::utc('2026-09-01'), null, null, Context::createDefaultContext()),
        );
    }

    public function testMissingHistogramYieldsEmptySeries(): void
    {
        $orders = $this->repository(static fn (): AggregationResultCollection => new AggregationResultCollection([]));
        $metrics = new OrderMetricsRepository($this->registry(['order' => $orders]));

        static::assertSame([], $metrics->series(MetricName::Sales, Granularity::Day, self::utc('2026-08-01'), self::utc('2026-08-02'), null, null, Context::createDefaultContext()));
    }

    public function testSegmentParsing(): void
    {
        static::assertNull(MetricsSegment::parse(''));
        static::assertSame('category:' . self::CATEGORY_ID, MetricsSegment::parse('Category: ' . strtoupper(self::CATEGORY_ID))?->toString());

        foreach (['category', 'brand:' . self::CATEGORY_ID, 'category:nope'] as $invalid) {
            try {
                MetricsSegment::parse($invalid);
                static::fail($invalid . ' accepted');
            } catch (\InvalidArgumentException) {
                $this->addToAssertionCount(1);
            }
        }
    }

    public function testMetricNotes(): void
    {
        static::assertFalse(MetricName::Traffic->isMeasured());
        static::assertFalse(MetricName::Conversion->isMeasured());
        static::assertTrue(MetricName::Sales->isMeasured());
        static::assertNull(MetricName::Sales->unavailableNote());
        static::assertStringContainsString('traffic', (string) MetricName::Traffic->unavailableNote());
        static::assertSame('week', Granularity::Week->toDateHistogramInterval());
    }

    /**
     * @param callable(Criteria): AggregationResultCollection $aggregate
     *
     * @return EntityRepository<EntityCollection<Entity>>
     */
    private function repository(callable $aggregate): EntityRepository
    {
        $repository = $this->getMockBuilder(EntityRepository::class)->disableOriginalConstructor()->getMock();
        $repository->method('aggregate')->willReturnCallback($aggregate);

        return $repository;
    }

    /**
     * A period boundary in UTC. The repository formats the boundaries it is given with
     * their own offset (ReportingPeriod builds them in the clock's zone); the shop container
     * runs date.timezone=Europe/Berlin, so a naive `new \DateTimeImmutable('2026-08-01')`
     * would carry +02:00 there and +00:00 on a UTC host.
     */
    private static function utc(string $date): \DateTimeImmutable
    {
        return new \DateTimeImmutable($date, new \DateTimeZone('UTC'));
    }

    /**
     * @param array<string, EntityRepository<EntityCollection<Entity>>> $repositories
     */
    private function registry(array $repositories): DefinitionInstanceRegistry
    {
        $registry = $this->createMock(DefinitionInstanceRegistry::class);
        $registry->method('getRepository')->willReturnCallback(static function (string $entity) use ($repositories): EntityRepository {
            if (!isset($repositories[$entity])) {
                throw new \LogicException('unexpected repository ' . $entity);
            }

            return $repositories[$entity];
        });

        return $registry;
    }
}
