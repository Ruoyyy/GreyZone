<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Mcp\Admin;

use Doctrine\DBAL\Connection;
use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\TestCase;
use Psr\Clock\ClockInterface;
use Swag\CommerceAgentTools\Analytics\OrderMetricsRepository;
use Swag\CommerceAgentTools\Mcp\Admin\BusinessSnapshotTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeApplyTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeDiscardTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeListTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeStageTool;
use Swag\CommerceAgentTools\Mcp\Admin\MetricsSeriesTool;
use Swag\CommerceAgentTools\StagedChange\ChangePlanner;
use Swag\CommerceAgentTools\StagedChange\ProductSnapshotLoader;
use Swag\CommerceAgentTools\StagedChange\StagedChangeService;
use Swag\CommerceAgentTools\StagedChange\StagedChangeStateMachine;
use Swag\CommerceAgentTools\Tests\Unit\Support\ChangeFixtures;

/**
 * Every admin tool must refuse before touching any collaborator when the
 * context lacks the workflow privilege.
 *
 * @internal
 */
#[CoversClass(ChangeStageTool::class)]
#[CoversClass(ChangeListTool::class)]
#[CoversClass(ChangeApplyTool::class)]
#[CoversClass(ChangeDiscardTool::class)]
#[CoversClass(BusinessSnapshotTool::class)]
#[CoversClass(MetricsSeriesTool::class)]
class AclEnforcementTest extends TestCase
{
    public function testChangeStageDenied(): void
    {
        $service = $this->createMock(StagedChangeService::class);
        $service->expects($this->never())->method('stage');
        $loader = $this->createMock(ProductSnapshotLoader::class);
        $loader->expects($this->never())->method('load');

        $tool = new ChangeStageTool(
            ChangeFixtures::contextProvider($this, ChangeFixtures::adminContext(['product:update'])),
            new ChangePlanner(),
            $loader,
            $service,
            $this->createMock(Connection::class),
        );

        $this->assertDenied(($tool)('inventory_action', '[]', 'x'), 'agent_change:create');
    }

    public function testChangeStageDeniedWithoutLedgerPrivilege(): void
    {
        $tool = new ChangeStageTool(
            ChangeFixtures::contextProvider($this, ChangeFixtures::adminContext(['agent_change:create'])),
            new ChangePlanner(),
            $this->createMock(ProductSnapshotLoader::class),
            $this->createMock(StagedChangeService::class),
            $this->createMock(Connection::class),
        );

        $this->assertDenied(($tool)('inventory_action', '[]', 'x'), 'swag_agent_staged_change:create');
    }

    public function testChangeListDenied(): void
    {
        $service = $this->createMock(StagedChangeService::class);
        $service->expects($this->never())->method('list');

        $tool = new ChangeListTool(ChangeFixtures::contextProvider($this, ChangeFixtures::adminContext([])), $service);

        $this->assertDenied(($tool)(), 'agent_change:read');
    }

    public function testChangeApplyDenied(): void
    {
        $service = $this->createMock(StagedChangeService::class);
        $service->expects($this->never())->method('find');
        $service->expects($this->never())->method('apply');

        $tool = new ChangeApplyTool(
            ChangeFixtures::contextProvider($this, ChangeFixtures::adminContext(['agent_change:read', 'agent_change:create'])),
            $service,
            new StagedChangeStateMachine(),
            $this->createMock(Connection::class),
        );

        $this->assertDenied(($tool)(ChangeFixtures::CHANGE_ID, false), 'agent_change:update');
    }

    public function testChangeApplyDeniedWithoutTargetEntityPrivilege(): void
    {
        $context = ChangeFixtures::adminContext(['agent_change:update', 'swag_agent_staged_change:update', 'swag_agent_staged_change:read']);
        $service = $this->createMock(StagedChangeService::class);
        $service->method('find')->willReturn(ChangeFixtures::change());
        $service->expects($this->never())->method('apply');

        $tool = new ChangeApplyTool(ChangeFixtures::contextProvider($this, $context), $service, new StagedChangeStateMachine(), $this->createMock(Connection::class));

        $this->assertDenied(($tool)(ChangeFixtures::CHANGE_ID, false), 'product:update');
    }

    public function testChangeDiscardDenied(): void
    {
        $service = $this->createMock(StagedChangeService::class);
        $service->expects($this->never())->method('discard');

        $tool = new ChangeDiscardTool(
            ChangeFixtures::contextProvider($this, ChangeFixtures::adminContext(['agent_change:read'])),
            $service,
            new StagedChangeStateMachine(),
        );

        $this->assertDenied(($tool)(ChangeFixtures::CHANGE_ID, false), 'agent_change:update');
    }

    public function testBusinessSnapshotDenied(): void
    {
        $metrics = $this->createMock(OrderMetricsRepository::class);
        $metrics->expects($this->never())->method('totals');

        $tool = new BusinessSnapshotTool(ChangeFixtures::contextProvider($this, ChangeFixtures::adminContext([])), $metrics, $this->createMock(ClockInterface::class));

        $this->assertDenied(($tool)(), 'order:read');
    }

    public function testMetricsSeriesDeniedWithoutLineItemPrivilege(): void
    {
        $metrics = $this->createMock(OrderMetricsRepository::class);
        $metrics->expects($this->never())->method('series');

        $tool = new MetricsSeriesTool(ChangeFixtures::contextProvider($this, ChangeFixtures::adminContext(['order:read'])), $metrics, $this->createMock(ClockInterface::class));

        $this->assertDenied(($tool)('sales'), 'order_line_item:read');
    }

    private function assertDenied(string $output, string $privilege): void
    {
        $data = json_decode($output, true, 512, \JSON_THROW_ON_ERROR);

        static::assertFalse($data['success']);
        static::assertSame('Missing privilege: ' . $privilege, $data['error']);
    }
}
