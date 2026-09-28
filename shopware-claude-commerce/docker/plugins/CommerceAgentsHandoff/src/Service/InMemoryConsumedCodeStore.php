<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff\Service;

/**
 * Process-local store — the exact semantics of `HandoffCodeVerifier._consumed` in
 * shopware_common/handoff.py. Used by the PHPUnit tests; the shop uses the database.
 */
final class InMemoryConsumedCodeStore implements ConsumedCodeStore
{
    /** @var array<string, int> jti => expires_at */
    private array $consumed = [];

    public function consume(string $jti, int $expiresAt, int $now): bool
    {
        foreach ($this->consumed as $storedJti => $storedExpiry) {
            if ($storedExpiry < $now) {
                unset($this->consumed[$storedJti]);
            }
        }

        if (\array_key_exists($jti, $this->consumed)) {
            return false;
        }

        $this->consumed[$jti] = $expiresAt;

        return true;
    }

    public function count(): int
    {
        return \count($this->consumed);
    }
}
