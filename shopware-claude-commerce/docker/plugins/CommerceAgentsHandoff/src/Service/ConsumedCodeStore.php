<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff\Service;

/**
 * Single-use store for handoff code ids (`jti`). Mirrors the `_consumed` dict of the
 * Python reference verifier: entries live until the code's `exp`, then they are purged.
 */
interface ConsumedCodeStore
{
    /**
     * Records `$jti` as used. Returns false when it was already recorded (replay).
     * Implementations purge entries whose `expires_at` is before `$now` on every call.
     *
     * @param string $jti 32 lowercase hex characters
     * @param int $expiresAt unix timestamp of the code's `exp`
     * @param int $now current unix timestamp
     */
    public function consume(string $jti, int $expiresAt, int $now): bool;
}
