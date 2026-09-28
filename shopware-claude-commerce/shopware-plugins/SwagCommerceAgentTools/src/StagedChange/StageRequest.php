<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

/**
 * Metadata the agent attaches to a change when staging it.
 */
final class StageRequest
{
    /**
     * @param list<array<string, mixed>> $items the agent's original items (kept for audit)
     * @param list<mixed>|null $guardrailNotes host-side guardrail results (usually strings)
     */
    public function __construct(
        public readonly string $summary,
        public readonly ?string $note,
        public readonly array $items,
        public readonly ?array $guardrailNotes = null,
        public readonly ?string $salesChannelId = null,
        public readonly ?string $currency = null,
        public readonly ?float $marginBeforePct = null,
        public readonly ?float $marginAfterPct = null,
        public readonly ?float $minMarginPct = null,
    ) {
    }
}
