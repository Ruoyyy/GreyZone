<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\Admin;

use Doctrine\DBAL\Connection;
use Mcp\Capability\Attribute\McpTool;
use Shopware\Core\Framework\Mcp\Attribute\McpToolDependsOn;
use Shopware\Core\Framework\Mcp\Attribute\McpToolGroup;
use Shopware\Core\Framework\Mcp\Attribute\McpToolRequires;
use Shopware\Core\Framework\Mcp\Context\McpContextProvider;
use Shopware\Core\Framework\Mcp\Tool\McpToolResponse;
use Shopware\Core\Framework\Uuid\Uuid;
use Swag\CommerceAgentTools\StagedChange\AgentChangePrivileges;
use Swag\CommerceAgentTools\StagedChange\ChangeStatus;
use Swag\CommerceAgentTools\StagedChange\StagedChangeException;
use Swag\CommerceAgentTools\StagedChange\StagedChangeService;
use Swag\CommerceAgentTools\StagedChange\StagedChangeStateMachine;

/**
 * @experimental stableVersion:v6.8.0 feature:MCP_SERVER
 */
#[McpTool(name: 'agent-change-apply', title: 'Apply Agent Change', description: 'Approves a staged agent change and writes it to the live catalog. Refuses changes that are not in status "staged". dryRun=true (default) re-runs the write in a rolled-back transaction and returns the preview without changing anything; dryRun=false performs the write, marks the change as applied and stamps the approving principal. Only the approver should call this with dryRun=false; the agent that staged the change must not.')]
#[McpToolGroup('agent-merchant')]
#[McpToolDependsOn('agent-change-list')]
#[McpToolRequires(AgentChangePrivileges::WORKFLOW_UPDATE)]
#[McpToolRequires(AgentChangePrivileges::LEDGER_UPDATE)]
#[McpToolRequires('product:update')]
class ChangeApplyTool extends McpToolResponse
{
    /**
     * @internal
     */
    public function __construct(
        private readonly McpContextProvider $contextProvider,
        private readonly StagedChangeService $changeService,
        private readonly StagedChangeStateMachine $stateMachine,
        private readonly Connection $connection,
    ) {
    }

    public function __invoke(string $changeId, bool $dryRun = true): string
    {
        $context = $this->contextProvider->getContext();

        if ($error = $this->requirePrivilege($context, ...AgentChangePrivileges::forTransition())) {
            return $error;
        }

        $changeId = strtolower(trim($changeId));
        if (!Uuid::isValid($changeId)) {
            return $this->error('"changeId" must be the UUID of a staged change (see agent-change-list).');
        }

        try {
            $change = $this->changeService->find($changeId, $context);
        } catch (StagedChangeException $e) {
            return $this->error($e->getMessage());
        }

        $status = ChangeStatus::from($change->getStatus());
        if (!$this->stateMachine->canTransition($status, ChangeStatus::Applied)) {
            return $this->error(StagedChangeException::invalidTransition($changeId, $status->value, ChangeStatus::Applied->value)->getMessage());
        }

        $targetPrivilege = $change->getTargetEntity() . ':update';
        if ($error = $this->requirePrivilege($context, $targetPrivilege)) {
            return $error;
        }

        if ($dryRun) {
            return $this->executeWithDryRun($this->connection, $context, function () use ($change, $context): string {
                $this->changeService->executeWrite($change, $context);

                return $this->success($change->toToolArray(), [
                    'dryRun' => true,
                    'note' => 'Write validated and rolled back. Call again with dryRun=false to apply.',
                ]);
            });
        }

        try {
            $applied = $this->changeService->apply($change, $context);
        } catch (StagedChangeException $e) {
            return $this->error($e->getMessage());
        } catch (\Throwable $e) {
            return $this->error(\sprintf('Applying change "%s" failed; it remains staged and the error was recorded: %s', $changeId, $e->getMessage()));
        }

        return $this->success($applied->toToolArray(), ['dryRun' => false]);
    }
}
