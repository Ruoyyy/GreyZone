<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\Admin;

use Doctrine\DBAL\Connection;
use Mcp\Capability\Attribute\McpTool;
use Shopware\Core\Framework\Mcp\Attribute\McpToolGroup;
use Shopware\Core\Framework\Mcp\Attribute\McpToolRequires;
use Shopware\Core\Framework\Mcp\Context\McpContextProvider;
use Shopware\Core\Framework\Mcp\Tool\McpToolResponse;
use Shopware\Core\Framework\Uuid\Uuid;
use Swag\CommerceAgentTools\StagedChange\AgentChangePrivileges;
use Swag\CommerceAgentTools\StagedChange\ChangeKind;
use Swag\CommerceAgentTools\StagedChange\ChangePlanner;
use Swag\CommerceAgentTools\StagedChange\ProductSnapshotLoader;
use Swag\CommerceAgentTools\StagedChange\StagedChangeException;
use Swag\CommerceAgentTools\StagedChange\StagedChangeService;
use Swag\CommerceAgentTools\StagedChange\StageRequest;

/**
 * @experimental stableVersion:v6.8.0 feature:MCP_SERVER
 */
#[McpTool(name: 'agent-change-stage', title: 'Stage Agent Change', description: 'Proposes a catalog change for human approval instead of writing it: listing text (listing_update), prices (price_update) or stock/activation (inventory_action). Returns a server-computed before/after preview for every field, produced by running the real write in a rolled-back transaction. With dryRun=true (default) nothing is stored; dryRun=false records the change as "staged" for agent-change-apply. This tool never modifies products itself. Items are a JSON array; see the plugin README for the per-kind item shape.')]
#[McpToolGroup('agent-merchant')]
#[McpToolRequires(AgentChangePrivileges::WORKFLOW_CREATE)]
#[McpToolRequires(AgentChangePrivileges::LEDGER_CREATE)]
#[McpToolRequires('product:read')]
#[McpToolRequires('product:update')]
class ChangeStageTool extends McpToolResponse
{
    private const MAX_SUMMARY_LENGTH = 1000;
    private const MAX_NOTE_LENGTH = 4000;

    /**
     * @internal
     */
    public function __construct(
        private readonly McpContextProvider $contextProvider,
        private readonly ChangePlanner $planner,
        private readonly ProductSnapshotLoader $snapshotLoader,
        private readonly StagedChangeService $changeService,
        private readonly Connection $connection,
    ) {
    }

    public function __invoke(
        string $kind,
        string $items,
        string $summary,
        string $note = '',
        string $guardrailNotes = '',
        string $salesChannelId = '',
        string $currency = '',
        string $margins = '',
        bool $dryRun = true,
    ): string {
        $context = $this->contextProvider->getContext();

        if ($error = $this->requirePrivilege($context, ...AgentChangePrivileges::forStage())) {
            return $error;
        }

        $changeKind = ChangeKind::tryFrom($kind);
        if ($changeKind === null) {
            return $this->error(StagedChangeException::unknownKind($kind)->getMessage());
        }
        if (!$changeKind->isSupported()) {
            return $this->error(StagedChangeException::kindNotSupported($changeKind)->getMessage());
        }

        $summary = trim($summary);
        if ($summary === '' || mb_strlen($summary) > self::MAX_SUMMARY_LENGTH) {
            return $this->error(\sprintf('"summary" is required and must be at most %d characters. Describe the change in one sentence for the approver.', self::MAX_SUMMARY_LENGTH));
        }
        if (mb_strlen($note) > self::MAX_NOTE_LENGTH) {
            return $this->error(\sprintf('"note" must be at most %d characters.', self::MAX_NOTE_LENGTH));
        }
        if ($salesChannelId !== '' && !Uuid::isValid($salesChannelId)) {
            return $this->error('"salesChannelId" must be a sales channel UUID or empty.');
        }
        if ($currency !== '' && preg_match('/^[A-Z]{3}$/', $currency) !== 1) {
            return $this->error('"currency" must be a three-letter ISO code such as EUR, or empty.');
        }

        $decodedItems = $this->decodeJsonOrError($items, 'items');
        if (\is_string($decodedItems)) {
            return $decodedItems;
        }
        if (!\array_is_list($decodedItems)) {
            $decodedItems = [$decodedItems];
        }
        foreach ($decodedItems as $item) {
            if (!\is_array($item)) {
                return $this->error('Invalid "items": every item must be a JSON object.');
            }
        }
        /** @var list<array<string, mixed>> $decodedItems */
        $maxItems = $this->changeService->maxItemsPerChange();
        if (\count($decodedItems) > $maxItems) {
            return $this->error(StagedChangeException::tooManyItems(\count($decodedItems), $maxItems)->getMessage());
        }

        $decodedGuardrails = null;
        if (trim($guardrailNotes) !== '') {
            $decodedGuardrails = $this->decodeJsonOrError($guardrailNotes, 'guardrailNotes');
            if (\is_string($decodedGuardrails)) {
                return $decodedGuardrails;
            }
            if (!\array_is_list($decodedGuardrails)) {
                $decodedGuardrails = [$decodedGuardrails];
            }
        }

        $marginValues = $this->parseMargins($margins);
        if (\is_string($marginValues)) {
            return $marginValues;
        }

        try {
            $productIds = $this->planner->referencedProductIds($decodedItems);
            $snapshots = $this->snapshotLoader->load($productIds, $context);
            $plan = $this->planner->plan($changeKind, $decodedItems, $snapshots);
        } catch (StagedChangeException $e) {
            return $this->error($e->getMessage());
        }

        if ($error = $this->requirePrivilege($context, ...$plan->requiredPrivileges())) {
            return $error;
        }

        $previewResult = $this->executeWithDryRun($this->connection, $context, function () use ($plan, $context): string {
            $this->changeService->executePlan($plan, $context);

            return $this->success([]);
        });
        $decodedPreview = json_decode($previewResult, true);
        if (!\is_array($decodedPreview) || ($decodedPreview['success'] ?? false) !== true) {
            return $previewResult;
        }

        if ($dryRun) {
            return $this->success([
                'kind' => $plan->kind->value,
                'status' => 'preview',
                'summary' => $summary,
                'targetEntity' => $plan->targetEntity,
                'itemCount' => \count($plan->preview),
                'items' => $plan->preview,
            ], ['dryRun' => true, 'note' => 'Nothing was stored. Call again with dryRun=false to record the change for approval.']);
        }

        /** @var list<mixed>|null $decodedGuardrails */
        $request = new StageRequest(
            summary: $summary,
            note: trim($note) !== '' ? trim($note) : null,
            items: $decodedItems,
            guardrailNotes: $decodedGuardrails,
            salesChannelId: $salesChannelId !== '' ? $salesChannelId : null,
            currency: $currency !== '' ? $currency : null,
            marginBeforePct: $marginValues['before'],
            marginAfterPct: $marginValues['after'],
            minMarginPct: $marginValues['min'],
        );

        try {
            $change = $this->changeService->stage($plan, $request, $context);
        } catch (\Throwable $e) {
            return $this->error('The change could not be recorded: ' . $e->getMessage());
        }

        return $this->success($change->toToolArray(), ['dryRun' => false]);
    }

    /**
     * @return array{before: float|null, after: float|null, min: float|null}|string
     */
    private function parseMargins(string $margins): array|string
    {
        $result = ['before' => null, 'after' => null, 'min' => null];
        if (trim($margins) === '') {
            return $result;
        }

        $decoded = $this->decodeJsonOrError($margins, 'margins');
        if (\is_string($decoded)) {
            return $decoded;
        }

        foreach (array_keys($result) as $key) {
            $value = $decoded[$key] ?? null;
            if ($value === null) {
                continue;
            }
            if (!\is_int($value) && !\is_float($value)) {
                return $this->error(\sprintf('"margins.%s" must be a number (percent).', $key));
            }
            $result[$key] = (float) $value;
        }

        return $result;
    }
}
