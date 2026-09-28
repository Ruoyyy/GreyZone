<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Policy;

/**
 * Keyword scorer for policy pages. Deliberately simple and deterministic:
 * title hits weigh more than body hits, every query token that matches adds
 * a bonus, and the excerpt is a window around the first body hit so the agent
 * sees the relevant passage instead of the page header.
 */
class PolicyScorer
{
    public const DEFAULT_EXCERPT_LENGTH = 1200;
    public const MIN_TOKEN_LENGTH = 3;

    private const TITLE_HIT_WEIGHT = 3.0;
    private const BODY_HIT_WEIGHT = 1.0;
    private const TOKEN_COVERAGE_WEIGHT = 2.0;
    private const MAX_BODY_HITS_PER_TOKEN = 5;
    private const EXCERPT_LEAD_IN = 120;

    /** German and English function words that carry no policy intent. */
    private const STOPWORDS = [
        'der', 'die', 'das', 'und', 'oder', 'ich', 'wie', 'was', 'ist', 'ein', 'eine', 'einen', 'mit', 'für', 'von',
        'kann', 'bei', 'auf', 'zum', 'zur', 'den', 'dem', 'des', 'nicht', 'mein', 'meine', 'meinen', 'sich', 'wird',
        'the', 'and', 'for', 'what', 'how', 'can', 'with', 'you', 'your', 'are', 'does', 'will', 'from', 'about',
    ];

    /**
     * @param list<PolicyDocument> $documents
     *
     * @return list<PolicyMatch> sorted by descending score, only documents with a positive score
     */
    public function rank(string $query, array $documents, int $limit, int $excerptLength = self::DEFAULT_EXCERPT_LENGTH): array
    {
        $tokens = $this->tokenize($query);
        if ($tokens === [] || $limit < 1) {
            return [];
        }

        $matches = [];
        foreach ($documents as $document) {
            $match = $this->score($tokens, $document, $excerptLength);
            if ($match !== null) {
                $matches[] = $match;
            }
        }

        usort($matches, static fn (PolicyMatch $a, PolicyMatch $b): int => $b->score <=> $a->score ?: strcmp($a->document->title, $b->document->title));

        return \array_slice($matches, 0, $limit);
    }

    /**
     * @return list<string>
     */
    public function tokenize(string $query): array
    {
        $normalized = mb_strtolower(trim($query));
        $raw = preg_split('/[^\p{L}\p{N}]+/u', $normalized) ?: [];

        $tokens = [];
        foreach ($raw as $token) {
            if (mb_strlen($token) < self::MIN_TOKEN_LENGTH || \in_array($token, self::STOPWORDS, true)) {
                continue;
            }
            $tokens[$token] = true;
        }

        return array_keys($tokens);
    }

    /**
     * @param list<string> $tokens
     */
    private function score(array $tokens, PolicyDocument $document, int $excerptLength): ?PolicyMatch
    {
        $title = mb_strtolower($document->title);
        $body = mb_strtolower($document->content);

        $score = 0.0;
        $matchedTokens = 0;
        $firstBodyHit = null;

        foreach ($tokens as $token) {
            $tokenMatched = false;

            if (mb_strpos($title, $token) !== false) {
                $score += self::TITLE_HIT_WEIGHT;
                $tokenMatched = true;
            }

            $bodyHits = mb_substr_count($body, $token);
            if ($bodyHits > 0) {
                $score += self::BODY_HIT_WEIGHT * min($bodyHits, self::MAX_BODY_HITS_PER_TOKEN);
                $tokenMatched = true;

                $position = mb_strpos($body, $token);
                if ($position !== false && ($firstBodyHit === null || $position < $firstBodyHit)) {
                    $firstBodyHit = $position;
                }
            }

            if ($tokenMatched) {
                ++$matchedTokens;
            }
        }

        if ($matchedTokens === 0) {
            return null;
        }

        $score += self::TOKEN_COVERAGE_WEIGHT * ($matchedTokens / \count($tokens));

        return new PolicyMatch($document, $score, $this->excerpt($document->content, $firstBodyHit, $excerptLength));
    }

    private function excerpt(string $content, ?int $firstHit, int $excerptLength): string
    {
        $length = mb_strlen($content);
        if ($length <= $excerptLength) {
            return $content;
        }

        $start = 0;
        if ($firstHit !== null) {
            $start = max(0, $firstHit - self::EXCERPT_LEAD_IN);
            $start = min($start, max(0, $length - $excerptLength));
        }

        $slice = mb_substr($content, $start, $excerptLength);
        $prefix = $start > 0 ? '…' : '';
        $suffix = $start + $excerptLength < $length ? '…' : '';

        return $prefix . trim($slice) . $suffix;
    }
}
