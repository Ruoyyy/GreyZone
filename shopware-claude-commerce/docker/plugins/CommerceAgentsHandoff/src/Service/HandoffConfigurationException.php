<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff\Service;

/**
 * COMMERCE_AGENTS_HANDOFF_SECRET is missing or too short. Raised when the verifier is
 * built, i.e. on the first request to /claude-commerce/continue — the rest of the shop
 * keeps working, the handoff route fails loudly.
 */
final class HandoffConfigurationException extends \InvalidArgumentException
{
    public static function secretTooShort(int $minimumBytes): self
    {
        return new self(sprintf(
            'COMMERCE_AGENTS_HANDOFF_SECRET must be at least %d bytes (docker/bootstrap.sh generates it)',
            $minimumBytes
        ));
    }
}
