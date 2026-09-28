<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Mcp\Admin;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\TestCase;
use Psr\Clock\ClockInterface;
use Shopware\Core\Framework\Context;
use Swag\CommerceAgentTools\Analytics\Granularity;
use Swag\CommerceAgentTools\Analytics\MetricName;
use Swag\CommerceAgentTools\Analytics\OrderMetricsRepository;
use Swag\CommerceAgentTools\Mcp\Admin\BusinessSnapshotTool;
use Swag\CommerceAgentTools\Mcp\Admin\MetricsSeriesTool;
use Swag\CommerceAgentTools\Tests\Unit\Support\ChangeFixtures;

/**
 * @internal
 */
#[CoversClass(BusinessSnapshotTool::class)]
#[CoversClass(MetricsSeriesTool::class)]
class AnalyticsToolsTest extends TestCase
{
    private Context $context;

    private ClockInterface $clock;

    protected function setUp(): void
    {
        $this->context = ChangeFixtures::adminContext(['order:read', 'order_line_item:read']);
        $clock = $this->createMock(ClockInterface::class);
        $clock->method('now')->willReturn(new \DateTimeImmutable('2026-09-03 13:11:00', new \DateTimeZone('Europe/Berlin')));
        $this->clock = $clock;
    }

    public function testSnapshotComparesWithPreviousPeriodAndFlagsUnmeasuredMetrics(): void
    {
        $metrics = $this->createMock(OrderMetricsRepository::class);
        $metrics->method('totals')->willReturnCallback(static function (\DateTimeImmutable $from): array {
            return $from->format('Y-m-d') === '2026-08-05'
                ? ['revenue' => 1250.0, 'orders' => 10, 'aov' => 125.0]
                : ['revenue' => 1000.0, 'orders' => 8, 'aov' => 125.0];
        });
        $metrics->method('units')->willReturnCallback(static fn (\DateTimeImmutable $from): int => $from->format('Y-m-d') === '2026-08-05' ? 30 : 20);

        $tool = new BusinessSnapshotTool(ChangeFixtures::contextProvider($this, $this->context), $metrics, $this->clock);
        $data = $this->decode(($tool)('30d', ChangeFixtures::CHANGE_ID));

        static::assertTrue($data['success']);
        static::assertSame('30d', $data['data']['period']['label']);
        static::assertSame(['current' => 1250.0, 'previous' => 1000.0, 'deltaPct' => 25.0], $data['data']['metrics']['sales']);
        static::assertSame(['current' => 10.0, 'previous' => 8.0, 'deltaPct' => 25.0], $data['data']['metrics']['orders']);
        static::assertSame(['current' => 125.0, 'previous' => 125.0, 'deltaPct' => 0.0], $data['data']['metrics']['aov']);
        static::assertSame(['current' => 30.0, 'previous' => 20.0, 'deltaPct' => 50.0], $data['data']['metrics']['units']);
        static::assertNull($data['data']['metrics']['traffic']['current']);
        static::assertStringContainsString('does not record storefront traffic', $data['data']['metrics']['traffic']['note']);
        static::assertNull($data['data']['metrics']['conversion']['current']);
        static::assertSame(ChangeFixtures::CHANGE_ID, $data['_meta']['salesChannelId']);
    }

    public function testSnapshotValidatesPeriodAndSalesChannel(): void
    {
        $metrics = $this->createMock(OrderMetricsRepository::class);
        $metrics->expects($this->never())->method('totals');
        $tool = new BusinessSnapshotTool(ChangeFixtures::contextProvider($this, $this->context), $metrics, $this->clock);

        static::assertStringContainsString('Unknown period "quarterly"', $this->decode(($tool)('quarterly'))['error']);
        static::assertStringContainsString('"salesChannelId" must be', $this->decode(($tool)('30d', 'abc'))['error']);
    }

    public function testSnapshotReportsAggregationFailures(): void
    {
        $metrics = $this->createMock(OrderMetricsRepository::class);
        $metrics->method('totals')->willThrowException(new \RuntimeException('db gone'));
        $tool = new BusinessSnapshotTool(ChangeFixtures::contextProvider($this, $this->context), $metrics, $this->clock);

        $data = $this->decode(($tool)());

        static::assertFalse($data['success']);
        static::assertSame('Order aggregation failed: db gone', $data['error']);
    }

    public function testSeriesDelegatesWithParsedArguments(): void
    {
        $metrics = $this->createMock(OrderMetricsRepository::class);
        $metrics->expects($this->once())->method('series')
            ->with(
                MetricName::Sales,
                Granularity::Week,
                $this->callback(static fn (\DateTimeImmutable $from): bool => $from->format('Y-m-d') === '2026-08-01'),
                $this->callback(static fn (\DateTimeImmutable $to): bool => $to->format('Y-m-d') === '2026-09-01'),
                null,
                $this->callback(static fn ($segment): bool => $segment !== null && $segment->toString() === 'category:' . ChangeFixtures::PRODUCT_ID),
                $this->context,
            )
            ->willReturn([['date' => '2026-08-03 00:00:00', 'value' => 10.0]]);

        $tool = new MetricsSeriesTool(ChangeFixtures::contextProvider($this, $this->context), $metrics, $this->clock);
        $data = $this->decode(($tool)('Sales', 'last_month', 'week', 'category:' . ChangeFixtures::PRODUCT_ID));

        static::assertTrue($data['success']);
        static::assertSame([['date' => '2026-08-03 00:00:00', 'value' => 10.0]], $data['data']['series']);
        static::assertNull($data['data']['note']);
        static::assertSame('sales', $data['_meta']['metric']);
        static::assertSame('week', $data['_meta']['granularity']);
        static::assertSame(1, $data['_meta']['buckets']);
    }

    public function testSeriesReturnsEmptySeriesWithNoteForTraffic(): void
    {
        $metrics = $this->createMock(OrderMetricsRepository::class);
        $metrics->expects($this->never())->method('series');
        $tool = new MetricsSeriesTool(ChangeFixtures::contextProvider($this, $this->context), $metrics, $this->clock);

        $data = $this->decode(($tool)('traffic'));

        static::assertTrue($data['success']);
        static::assertSame([], $data['data']['series']);
        static::assertStringContainsString('does not record storefront traffic', $data['data']['note']);
    }

    public function testSeriesValidatesInputs(): void
    {
        $metrics = $this->createMock(OrderMetricsRepository::class);
        $metrics->expects($this->never())->method('series');
        $tool = new MetricsSeriesTool(ChangeFixtures::contextProvider($this, $this->context), $metrics, $this->clock);

        static::assertStringContainsString('Unknown metric "revenue"', $this->decode(($tool)('revenue'))['error']);
        static::assertStringContainsString('Unknown granularity "hour"', $this->decode(($tool)('sales', '7d', 'hour'))['error']);
        static::assertStringContainsString('Segments have the form', $this->decode(($tool)('sales', '7d', 'day', 'category'))['error']);
        static::assertStringContainsString('at most 400 days', $this->decode(($tool)('sales', '2025-01-01..2026-06-30', 'day'))['error']);
    }

    /**
     * @return array<string, mixed>
     */
    private function decode(string $output): array
    {
        $data = json_decode($output, true, 512, \JSON_THROW_ON_ERROR);
        static::assertIsArray($data);

        return $data;
    }
}
