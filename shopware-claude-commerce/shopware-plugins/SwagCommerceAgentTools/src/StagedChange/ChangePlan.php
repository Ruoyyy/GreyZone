<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

/**
 * The executable form of a staged change: one DAL upsert payload per touched
 * entity row plus the human-readable before/after preview.
 *
 * @phpstan-type PreviewRow array{target: string, targetLabel: string, field: string, before: mixed, after: mixed, currencyId?: string}
 */
final class ChangePlan
{
    /**
     * @param list<array<string, mixed>> $payload upsert payload for the target entity
     * @param list<PreviewRow> $preview
     */
    public function __construct(
        public readonly ChangeKind $kind,
        public readonly string $targetEntity,
        public readonly array $payload,
        public readonly array $preview,
    ) {
    }

    /**
     * @return list<string>
     */
    public function requiredPrivileges(): array
    {
        return [$this->targetEntity . ':read', $this->targetEntity . ':update'];
    }
}
