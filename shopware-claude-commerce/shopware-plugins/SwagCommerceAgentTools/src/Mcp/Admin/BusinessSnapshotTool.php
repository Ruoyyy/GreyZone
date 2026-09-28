<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\Admin;

use Mcp\Capability\Attribute\McpTool;
use Psr\Clock\ClockInterface;
use Shopware\Core\Framework\Mcp\Attribute\McpToolGroup;
use Shopware\Core\Framework\Mcp\Attribute\McpToolRequires;
use Shopware\Core\Framework\Mcp\Context\McpContextProvider;
use Shopware\Core\Framework\Mcp\Tool\McpToolResponse;
use Shopware\Core\Framework\Uuid\Uuid;
use Swag\CommerceAgentTools\Analytics\MetricName;
use Swag\CommerceAgentTools\Analytics\OrderMetricsRepository;
use Swag\CommerceAgentTools\Analytics\ReportingPeriod;

/**
 * @experimental stableVersion:v6.8.0 feature:MCP_SERVER
 */
#[McpTool(name: 'agent-business-snapshot', title: 'Business Snapshot', description: 'The merchant\'s daily digest numbers in one call: revenue, order count, average order value and units sold for a period (default 30d) with the change versus the previous period. Traffic and conversion rate are returned as null with a note because Shopware core does not measure them. Use agent-metrics-series when a value per day, week or month is needed.')]
#[McpToolGroup('agent-merchant')]
#[McpToolRequires('order:read')]
#[McpToolRequires('order_line_item:read')]
class BusinessSnapshotTool extends McpToolResponse
{
    /**
     * @internal
     */
    public function __construct(
        private readonly McpContextProvider $contextProvider,
        private readonly OrderMetricsRepository $metrics,
        private readonly ClockInterface $clock,
    ) {
    }

    public function __invoke(string $period = ReportingPeriod::DEFAULT, string $salesChannelId = ''): string
    {
        $context = $this->contextProvider->getContext();

        if ($error = $this->requirePrivilege($context, 'order:read', 'order_line_item:read')) {
            return $error;
        }

        $salesChannelId = strtolower(trim($salesChannelId));
        if ($salesChannelId !== '' && !Uuid::isValid($salesChannelId)) {
            return $this->error('"salesChannelId" must be a sales channel UUID or empty for all channels.');
        }
        $scope = $salesChannelId !== '' ? $salesChannelId : null;

        try {
            $range = ReportingPeriod::parse($period, $this->clock->now());
        } catch (\InvalidArgumentException $e) {
            return $this->error($e->getMessage());
        }

        try {
            $current = $this->metrics->totals($range->from, $range->to, $scope, null, $context);
            $previous = $this->metrics->totals($range->previousFrom, $range->previousTo, $scope, null, $context);
            $currentUnits = $this->metrics->units($range->from, $range->to, $scope, null, $context);
            $previousUnits = $this->metrics->units($range->previousFrom, $range->previousTo, $scope, null, $context);
        } catch (\Throwable $e) {
            return $this->error('Order aggregation failed: ' . $e->getMessage());
        }

        $notes = [
            MetricName::Traffic->unavailableNote(),
            MetricName::Conversion->unavailableNote(),
            'Revenue sums order totals in each order\'s own currency; filter by salesChannelId for a single-currency view.',
        ];

        return $this->success([
            'period' => $range->toArray(),
            'metrics' => [
                'sales' => $this->metric($current['revenue'], $previous['revenue']),
                'orders' => $this->metric((float) $current['orders'], (float) $previous['orders']),
                'aov' => $this->metric($current['aov'], $previous['aov']),
                'units' => $this->metric((float) $currentUnits, (float) $previousUnits),
                'traffic' => ['current' => null, 'previous' => null, 'deltaPct' => null, 'note' => MetricName::Traffic->unavailableNote()],
                'conversion' => ['current' => null, 'previous' => null, 'deltaPct' => null, 'note' => MetricName::Conversion->unavailableNote()],
            ],
            'notes' => array_values(array_filter($notes)),
        ], [
            'salesChannelId' => $scope,
            'source' => 'order-aggregation',
        ]);
    }

    /**
     * @return array{current: float|null, previous: float|null, deltaPct: float|null}
     */
    private function metric(?float $current, ?float $previous): array
    {
        return [
            'current' => $current,
            'previous' => $previous,
            'deltaPct' => $current !== null && $previous !== null ? ReportingPeriod::deltaPct($current, $previous) : null,
        ];
    }
}
