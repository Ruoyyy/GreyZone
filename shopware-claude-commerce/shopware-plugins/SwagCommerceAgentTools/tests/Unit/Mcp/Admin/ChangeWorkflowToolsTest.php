<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Mcp\Admin;

use Doctrine\DBAL\Connection;
use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\DataAbstractionLayer\Event\EntityWrittenContainerEvent;
use Shopware\Core\Framework\Event\NestedEventCollection;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeApplyTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeDiscardTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeListTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeStageTool;
use Swag\CommerceAgentTools\StagedChange\ChangePlan;
use Swag\CommerceAgentTools\StagedChange\ChangePlanner;
use Swag\CommerceAgentTools\StagedChange\ChangeStatus;
use Swag\CommerceAgentTools\StagedChange\Entity\AgentStagedChangeEntity;
use Swag\CommerceAgentTools\StagedChange\ProductSnapshot;
use Swag\CommerceAgentTools\StagedChange\ProductSnapshotLoader;
use Swag\CommerceAgentTools\StagedChange\StagedChangeException;
use Swag\CommerceAgentTools\StagedChange\StagedChangeService;
use Swag\CommerceAgentTools\StagedChange\StagedChangeStateMachine;
use Swag\CommerceAgentTools\Tests\Unit\Support\ChangeFixtures;

/**
 * @internal
 */
#[CoversClass(ChangeStageTool::class)]
#[CoversClass(ChangeApplyTool::class)]
#[CoversClass(ChangeDiscardTool::class)]
#[CoversClass(ChangeListTool::class)]
class ChangeWorkflowToolsTest extends TestCase
{
    private const STAGE_PRIVILEGES = ['agent_change:create', 'swag_agent_staged_change:create', 'swag_agent_staged_change:read', 'product:read', 'product:update'];
    private const TRANSITION_PRIVILEGES = ['agent_change:read', 'agent_change:update', 'swag_agent_staged_change:update', 'swag_agent_staged_change:read', 'product:update'];

    private Context $context;

    /** @var StagedChangeService&MockObject */
    private StagedChangeService $service;

    /** @var Connection&MockObject */
    private Connection $connection;

    protected function setUp(): void
    {
        $this->context = ChangeFixtures::adminContext(array_merge(self::STAGE_PRIVILEGES, self::TRANSITION_PRIVILEGES));
        $this->service = $this->createMock(StagedChangeService::class);
        $this->service->method('maxItemsPerChange')->willReturn(50);
        $this->connection = $this->createMock(Connection::class);
    }

    public function testStageDryRunPreviewsInRolledBackTransactionAndStoresNothing(): void
    {
        $this->connection->expects($this->once())->method('beginTransaction');
        $this->connection->expects($this->once())->method('rollBack');
        $this->connection->expects($this->never())->method('commit');

        $this->service->expects($this->once())->method('executePlan')
            ->with($this->callback(static fn (ChangePlan $plan): bool => $plan->payload === [['id' => ChangeFixtures::PRODUCT_ID, 'stock' => 28]]))
            ->willReturn($this->writtenEvent());
        $this->service->expects($this->never())->method('stage');

        $output = ($this->stageTool())('inventory_action', json_encode([['productId' => ChangeFixtures::PRODUCT_ID, 'action' => 'restock', 'quantity' => 25]], \JSON_THROW_ON_ERROR), 'Restock stool');
        $data = $this->decode($output);

        static::assertTrue($data['success']);
        static::assertTrue($data['_meta']['dryRun']);
        static::assertSame('preview', $data['data']['status']);
        static::assertSame([[
            'target' => ChangeFixtures::PRODUCT_ID,
            'targetLabel' => 'Hocker',
            'field' => 'stock',
            'before' => 3,
            'after' => 28,
        ]], $data['data']['items']);
        static::assertFalse($this->context->hasState(Context::SKIP_TRIGGER_FLOW), 'dry-run state must be removed afterwards');
    }

    public function testStageWithDryRunFalseRecordsAfterSuccessfulPreview(): void
    {
        $this->service->method('executePlan')->willReturn($this->writtenEvent());
        $this->service->expects($this->once())->method('stage')->willReturn(ChangeFixtures::change());

        $output = ($this->stageTool())(
            'inventory_action',
            json_encode([['productId' => ChangeFixtures::PRODUCT_ID, 'action' => 'restock', 'quantity' => 25]], \JSON_THROW_ON_ERROR),
            'Restock stool',
            'Sells out weekly',
            '[{"rule":"restock_cap","status":"ok"}]',
            '',
            'EUR',
            '{"before": 20, "after": 31.5, "min": 15}',
            false,
        );
        $data = $this->decode($output);

        static::assertTrue($data['success']);
        static::assertFalse($data['_meta']['dryRun']);
        static::assertSame(ChangeFixtures::CHANGE_ID, $data['data']['changeId']);
        static::assertSame('staged', $data['data']['status']);
    }

    public function testStageDoesNotRecordWhenPreviewWriteFails(): void
    {
        $this->service->method('executePlan')->willThrowException(new \RuntimeException('Field "stock" is invalid'));
        $this->service->expects($this->never())->method('stage');

        $output = ($this->stageTool())('inventory_action', json_encode([['productId' => ChangeFixtures::PRODUCT_ID, 'action' => 'restock', 'quantity' => 25]], \JSON_THROW_ON_ERROR), 'Restock', '', '', '', '', '', false);
        $data = $this->decode($output);

        static::assertFalse($data['success']);
        static::assertSame('Field "stock" is invalid', $data['error']);
    }

    public function testStageRefusesPromotionKindBeforeLoadingAnything(): void
    {
        $loader = $this->createMock(ProductSnapshotLoader::class);
        $loader->expects($this->never())->method('load');

        $output = ($this->stageTool($loader))('promotion', '[{"productId":"' . ChangeFixtures::PRODUCT_ID . '"}]', 'Summer sale');
        $data = $this->decode($output);

        static::assertFalse($data['success']);
        static::assertStringContainsString('"promotion" is not supported', $data['error']);
        static::assertStringContainsString('listing_update, price_update, inventory_action', $data['error']);
    }

    public function testStageValidatesInputs(): void
    {
        $tool = $this->stageTool();

        static::assertStringContainsString('Unknown change kind "foo"', $this->decode(($tool)('foo', '[]', 'x'))['error']);
        static::assertStringContainsString('"summary" is required', $this->decode(($tool)('inventory_action', '[]', '  '))['error']);
        static::assertStringContainsString('Invalid JSON for "items"', $this->decode(($tool)('inventory_action', '{bad', 'x'))['error']);
        static::assertStringContainsString('"salesChannelId" must be', $this->decode(($tool)('inventory_action', '[]', 'x', '', '', 'nope'))['error']);
        static::assertStringContainsString('"currency" must be', $this->decode(($tool)('inventory_action', '[]', 'x', '', '', '', 'euro'))['error']);
        static::assertStringContainsString('"margins.after" must be a number', $this->decode(($tool)('inventory_action', '[]', 'x', '', '', '', '', '{"after":"high"}'))['error']);
        static::assertStringContainsString('at least one item', $this->decode(($tool)('inventory_action', '[]', 'x'))['error']);
    }

    public function testApplyDryRunRollsBackAndKeepsStatus(): void
    {
        $this->service->method('find')->willReturn(ChangeFixtures::change());
        $this->service->expects($this->once())->method('executeWrite')->willReturn($this->writtenEvent());
        $this->service->expects($this->never())->method('apply');
        $this->connection->expects($this->once())->method('beginTransaction');
        $this->connection->expects($this->once())->method('rollBack');

        $data = $this->decode(($this->applyTool())(ChangeFixtures::CHANGE_ID));

        static::assertTrue($data['success']);
        static::assertTrue($data['_meta']['dryRun']);
        static::assertSame('staged', $data['data']['status']);
    }

    public function testApplyWithDryRunFalseDelegatesToService(): void
    {
        $this->service->method('find')->willReturn(ChangeFixtures::change());
        $this->service->expects($this->once())->method('apply')->willReturn(ChangeFixtures::change(ChangeStatus::Applied));
        $this->connection->expects($this->never())->method('beginTransaction');

        $data = $this->decode(($this->applyTool())(ChangeFixtures::CHANGE_ID, false));

        static::assertTrue($data['success']);
        static::assertFalse($data['_meta']['dryRun']);
        static::assertSame('applied', $data['data']['status']);
    }

    public function testApplyRefusesAppliedAndDiscardedChangesWithoutWriting(): void
    {
        foreach ([ChangeStatus::Applied, ChangeStatus::Discarded] as $status) {
            $service = $this->createMock(StagedChangeService::class);
            $service->method('find')->willReturn(ChangeFixtures::change($status));
            $service->expects($this->never())->method('executeWrite');
            $service->expects($this->never())->method('apply');
            $connection = $this->createMock(Connection::class);
            $connection->expects($this->never())->method('beginTransaction');

            $tool = new ChangeApplyTool(ChangeFixtures::contextProvider($this, $this->context), $service, new StagedChangeStateMachine(), $connection);

            foreach ([true, false] as $dryRun) {
                $data = $this->decode(($tool)(ChangeFixtures::CHANGE_ID, $dryRun));
                static::assertFalse($data['success']);
                static::assertStringContainsString(\sprintf('is "%s" and cannot become "applied"', $status->value), $data['error']);
            }
        }
    }

    public function testApplyReportsUnknownChangeAndInvalidId(): void
    {
        $this->service->method('find')->willThrowException(StagedChangeException::notFound(ChangeFixtures::CHANGE_ID));
        $tool = $this->applyTool();

        static::assertStringContainsString('was not found', $this->decode(($tool)(ChangeFixtures::CHANGE_ID, false))['error']);
        static::assertStringContainsString('"changeId" must be the UUID', $this->decode(($tool)('nope', false))['error']);
    }

    public function testApplyReportsWriteFailureAndLeavesChangeStaged(): void
    {
        $this->service->method('find')->willReturn(ChangeFixtures::change());
        $this->service->method('apply')->willThrowException(new \RuntimeException('constraint violation'));

        $data = $this->decode(($this->applyTool())(ChangeFixtures::CHANGE_ID, false));

        static::assertFalse($data['success']);
        static::assertStringContainsString('remains staged', $data['error']);
        static::assertStringContainsString('constraint violation', $data['error']);
    }

    public function testDiscardDryRunAndCommit(): void
    {
        $this->service->method('find')->willReturn(ChangeFixtures::change());
        $this->service->expects($this->once())->method('discard')->willReturn(ChangeFixtures::change(ChangeStatus::Discarded));
        $tool = new ChangeDiscardTool(ChangeFixtures::contextProvider($this, $this->context), $this->service, new StagedChangeStateMachine());

        $preview = $this->decode(($tool)(ChangeFixtures::CHANGE_ID));
        static::assertTrue($preview['_meta']['dryRun']);
        static::assertSame('staged', $preview['data']['status']);

        $result = $this->decode(($tool)(ChangeFixtures::CHANGE_ID, false));
        static::assertFalse($result['_meta']['dryRun']);
        static::assertSame('discarded', $result['data']['status']);
    }

    public function testDiscardRefusesTerminalChanges(): void
    {
        $this->service->method('find')->willReturn(ChangeFixtures::change(ChangeStatus::Applied));
        $this->service->expects($this->never())->method('discard');
        $tool = new ChangeDiscardTool(ChangeFixtures::contextProvider($this, $this->context), $this->service, new StagedChangeStateMachine());

        $data = $this->decode(($tool)(ChangeFixtures::CHANGE_ID, false));

        static::assertFalse($data['success']);
        static::assertStringContainsString('cannot become "discarded"', $data['error']);
    }

    public function testListFiltersByStatusAndPaginates(): void
    {
        $this->service->expects($this->once())->method('list')
            ->with(ChangeStatus::Applied, 10, 2, $this->context)
            ->willReturn(ChangeFixtures::searchResult($this->context, ChangeFixtures::change(ChangeStatus::Applied)));

        $tool = new ChangeListTool(ChangeFixtures::contextProvider($this, $this->context), $this->service);
        $data = $this->decode(($tool)('applied', 10, 2));

        static::assertTrue($data['success']);
        static::assertCount(1, $data['data']);
        static::assertSame('applied', $data['data'][0]['status']);
        static::assertSame(['total' => 1, 'page' => 2, 'limit' => 10, 'status' => 'applied'], $data['_meta']);
    }

    public function testListAcceptsAllAndRejectsUnknownStatus(): void
    {
        $this->service->method('list')->with(null, 25, 1, $this->context)->willReturn(ChangeFixtures::searchResult($this->context));
        $tool = new ChangeListTool(ChangeFixtures::contextProvider($this, $this->context), $this->service);

        static::assertTrue($this->decode(($tool)('all'))['success']);
        static::assertStringContainsString('Invalid status "pending"', $this->decode(($tool)('pending'))['error']);
        static::assertStringContainsString('limit must be between', $this->decode(($tool)('staged', 0))['error']);
    }

    private function stageTool(?ProductSnapshotLoader $loader = null): ChangeStageTool
    {
        if ($loader === null) {
            $loader = $this->createMock(ProductSnapshotLoader::class);
            $loader->method('load')->willReturn([
                ChangeFixtures::PRODUCT_ID => new ProductSnapshot(ChangeFixtures::PRODUCT_ID, 'SW-1001', ['name' => 'Hocker'], 3, true, 19.0, []),
            ]);
        }

        return new ChangeStageTool(
            ChangeFixtures::contextProvider($this, $this->context),
            new ChangePlanner(),
            $loader,
            $this->service,
            $this->connection,
        );
    }

    private function applyTool(): ChangeApplyTool
    {
        return new ChangeApplyTool(ChangeFixtures::contextProvider($this, $this->context), $this->service, new StagedChangeStateMachine(), $this->connection);
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

    private function writtenEvent(): EntityWrittenContainerEvent
    {
        return new EntityWrittenContainerEvent($this->context, new NestedEventCollection([]), []);
    }
}
