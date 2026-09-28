<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

/**
 * A staged payload made current for the write: restock rows carry `current + delta`
 * instead of the absolute level previewed at staging time, and `notes` names every
 * row whose base moved in between (see StagedChangeService::rebaseRestock).
 */
final class RebasedPayload
{
    /**
     * @param list<array<string, mixed>> $payload
     * @param list<string> $notes
     */
    public function __construct(
        public readonly array $payload,
        public readonly array $notes,
    ) {
    }
}
