<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Policy;

final class PolicyMatch
{
    public function __construct(
        public readonly PolicyDocument $document,
        public readonly float $score,
        public readonly string $excerpt,
    ) {
    }

    /**
     * @return array{policy_id: string, title: string, category: string, content: string, url: string|null, score: float}
     */
    public function toArray(): array
    {
        return [
            'policy_id' => $this->document->policyId,
            'title' => $this->document->title,
            'category' => $this->document->category,
            'content' => $this->excerpt,
            'url' => $this->document->url,
            'score' => round($this->score, 3),
        ];
    }
}
