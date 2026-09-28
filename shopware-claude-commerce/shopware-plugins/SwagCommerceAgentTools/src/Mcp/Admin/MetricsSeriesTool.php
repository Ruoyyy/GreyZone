<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\Admin;

use Mcp\Capability\Attribute\McpTool;
use Psr\Clock\ClockInterface;
use Shopware\Core\Framework\Mcp\Attribute\McpToolGroup;
use Shopware\Core\Framework\Mcp\Attribute\McpToolRequires;
use Shopware\Core\Framework\Mcp\Context\McpContextProvider;
use Shopware\Core\Framework\Mcp\Tool\McpToolResponse;
use Shopware\Core\Framework\Uuid\Uuid;
use Swag\CommerceAgentTools\Analytics\Granularity;
use Swag\CommerceAgentTools\Analytics\MetricName;
use Swag\CommerceAgentTools\Analytics\MetricsSegment;
use Swag\CommerceAgentTools\Analytics\OrderMetricsRepository;
use Swag\CommerceAgentTools\Analytics\ReportingPeriod;

/**
 * @experimental stableVersion:v6.8.0 feature:MCP_SERVER
 */
#[McpTool(name: 'agent-metrics-series', title: 'Metrics Series', description: 'A time series of one business metric for charts and trend questions: sales, orders, aov (average order value) or units per day, week or month over a period (default 30d), optionally sliced by "category:<uuid>" or "sales_channel:<uuid>". Traffic and conversion return an empty series with a note. For a single headline figure with period comparison use agent-business-snapshot.')]
#[McpToolGroup('agent-merchant')]
#[McpToolRequires('order:read')]
#[McpToolRequires('order_line_item:read')]
class MetricsSeriesTool extends McpToolResponse
{
    private const MAX_BUCKETS = 400;

    /**
     * @internal
     */
    public function __construct(
        private readonly McpContextProvider $contextProvider,
        private readonly OrderMetricsRepository $metrics,
        private readonly ClockInterface $clock,
    ) {
    }

    public function __invoke(
        string $metric,
        string $period = ReportingPeriod::DEFAULT,
        string $granularity = 'day',
        string $segment = '',
        string $salesChannelId = '',
    ): string {
        $context = $this->contextProvider->getContext();

        if ($error = $this->requirePrivilege($context, 'order:read', 'order_line_item:read')) {
            return $error;
        }

        $metricName = MetricName::tryFrom(strtolower(trim($metric)));
        if ($metricName === null) {
            return $this->error(\sprintf('Unknown metric "%s". Allowed: %s.', $metric, implode(', ', MetricName::values())));
        }

        $interval = Granularity::tryFrom(strtolower(trim($granularity)));
        if ($interval === null) {
            return $this->error(\sprintf('Unknown granularity "%s". Allowed: %s.', $granularity, implode(', ', Granularity::values())));
        }

        $salesChannelId = strtolower(trim($salesChannelId));
        if ($salesChannelId !== '' && !Uuid::isValid($salesChannelId)) {
            return $this->error('"salesChannelId" must be a sales channel UUID or empty for all channels.');
        }
        $scope = $salesChannelId !== '' ? $salesChannelId : null;

        try {
            $range = ReportingPeriod::parse($period, $this->clock->now());
            $slice = MetricsSegment::parse($segment);
        } catch (\InvalidArgumentException $e) {
            return $this->error($e->getMessage());
        }

        if ($interval === Granularity::Day && $range->days() > self::MAX_BUCKETS) {
            return $this->error(\sprintf('A daily series may cover at most %d days; use granularity "week" or "month" for longer periods.', self::MAX_BUCKETS));
        }

        $meta = [
            'metric' => $metricName->value,
            'granularity' => $interval->value,
            'segment' => $slice?->toString(),
            'salesChannelId' => $scope,
            'source' => 'order-aggregation',
        ];

        if (!$metricName->isMeasured()) {
            return $this->success([
                'period' => $range->toArray(),
                'series' => [],
                'note' => $metricName->unavailableNote(),
            ], $meta);
        }

        try {
            $series = $this->metrics->series($metricName, $interval, $range->from, $range->to, $scope, $slice, $context);
        } catch (\Throwable $e) {
            return $this->error('Order aggregation failed: ' . $e->getMessage());
        }

        return $this->success([
            'period' => $range->toArray(),
            'series' => $series,
            'note' => null,
        ], $meta + ['buckets' => \count($series)]);
    }
}
