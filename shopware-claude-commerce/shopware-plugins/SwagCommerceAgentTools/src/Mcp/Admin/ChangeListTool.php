<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\Admin;

use Mcp\Capability\Attribute\McpTool;
use Shopware\Core\Framework\Mcp\Attribute\McpToolGroup;
use Shopware\Core\Framework\Mcp\Attribute\McpToolRequires;
use Shopware\Core\Framework\Mcp\Context\McpContextProvider;
use Shopware\Core\Framework\Mcp\Tool\McpToolResponse;
use Swag\CommerceAgentTools\StagedChange\AgentChangePrivileges;
use Swag\CommerceAgentTools\StagedChange\ChangeStatus;
use Swag\CommerceAgentTools\StagedChange\Entity\AgentStagedChangeEntity;
use Swag\CommerceAgentTools\StagedChange\StagedChangeService;

/**
 * @experimental stableVersion:v6.8.0 feature:MCP_SERVER
 */
#[McpTool(name: 'agent-change-list', title: 'List Agent Changes', description: 'Shows the changes agents have proposed for this shop and their approval state: pending (staged), already applied, or discarded. Each entry carries the before/after preview and who staged or approved it. Filter by status "staged" (default), "applied", "discarded" or "all". Read-only.')]
#[McpToolGroup('agent-merchant')]
#[McpToolRequires(AgentChangePrivileges::WORKFLOW_READ)]
#[McpToolRequires(AgentChangePrivileges::LEDGER_READ)]
class ChangeListTool extends McpToolResponse
{
    public const STATUS_ALL = 'all';

    private const DEFAULT_LIMIT = 25;
    private const MAX_LIMIT = 100;

    /**
     * @internal
     */
    public function __construct(
        private readonly McpContextProvider $contextProvider,
        private readonly StagedChangeService $changeService,
    ) {
    }

    public function __invoke(string $status = 'staged', int $limit = self::DEFAULT_LIMIT, int $page = 1): string
    {
        $context = $this->contextProvider->getContext();

        if ($error = $this->requirePrivilege($context, ...AgentChangePrivileges::forList())) {
            return $error;
        }

        $filter = null;
        if ($status !== self::STATUS_ALL) {
            $filter = ChangeStatus::tryFrom($status);
            if ($filter === null) {
                return $this->error(\sprintf('Invalid status "%s". Allowed: %s, %s.', $status, implode(', ', ChangeStatus::values()), self::STATUS_ALL));
            }
        }

        if ($limit < 1 || $limit > self::MAX_LIMIT) {
            return $this->error(\sprintf('limit must be between 1 and %d.', self::MAX_LIMIT));
        }
        if ($page < 1) {
            return $this->error('page must be >= 1.');
        }

        try {
            $result = $this->changeService->list($filter, $limit, $page, $context);
        } catch (\Throwable $e) {
            return $this->error($e->getMessage());
        }

        $changes = array_values(array_map(
            static fn (AgentStagedChangeEntity $change): array => $change->toToolArray(),
            $result->getEntities()->getElements(),
        ));

        return $this->success($changes, [
            'total' => $result->getTotal(),
            'page' => $page,
            'limit' => $limit,
            'status' => $status,
        ]);
    }
}
