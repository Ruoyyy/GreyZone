<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\Admin;

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
#[McpTool(name: 'agent-change-discard', title: 'Discard Agent Change', description: 'Rejects a staged agent change so it is never written; the catalog stays untouched. Refuses changes that are not in status "staged". dryRun=true (default) only reports what would happen; dryRun=false marks the change as discarded and stamps the principal.')]
#[McpToolGroup('agent-merchant')]
#[McpToolDependsOn('agent-change-list')]
#[McpToolRequires(AgentChangePrivileges::WORKFLOW_UPDATE)]
#[McpToolRequires(AgentChangePrivileges::LEDGER_UPDATE)]
class ChangeDiscardTool extends McpToolResponse
{
    /**
     * @internal
     */
    public function __construct(
        private readonly McpContextProvider $contextProvider,
        private readonly StagedChangeService $changeService,
        private readonly StagedChangeStateMachine $stateMachine,
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
        if (!$this->stateMachine->canTransition($status, ChangeStatus::Discarded)) {
            return $this->error(StagedChangeException::invalidTransition($changeId, $status->value, ChangeStatus::Discarded->value)->getMessage());
        }

        if ($dryRun) {
            return $this->success($change->toToolArray(), [
                'dryRun' => true,
                'note' => 'Nothing changed. Call again with dryRun=false to discard.',
            ]);
        }

        try {
            $discarded = $this->changeService->discard($change, $context);
        } catch (\Throwable $e) {
            return $this->error($e->getMessage());
        }

        return $this->success($discarded->toToolArray(), ['dryRun' => false]);
    }
}
