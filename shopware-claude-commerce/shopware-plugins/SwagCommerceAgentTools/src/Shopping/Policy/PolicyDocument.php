<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Policy;

/**
 * A single buyer-facing policy page (CMS category or landing page) reduced to plain text.
 */
final class PolicyDocument
{
    public function __construct(
        public readonly string $policyId,
        public readonly string $title,
        public readonly string $category,
        public readonly string $content,
        public readonly ?string $url = null,
    ) {
    }
}
