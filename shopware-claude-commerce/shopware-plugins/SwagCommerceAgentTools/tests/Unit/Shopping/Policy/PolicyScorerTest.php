<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Shopping\Policy;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\TestCase;
use Swag\CommerceAgentTools\Shopping\Policy\PolicyDocument;
use Swag\CommerceAgentTools\Shopping\Policy\PolicyMatch;
use Swag\CommerceAgentTools\Shopping\Policy\PolicyScorer;

/**
 * @internal
 */
#[CoversClass(PolicyScorer::class)]
#[CoversClass(PolicyMatch::class)]
#[CoversClass(PolicyDocument::class)]
class PolicyScorerTest extends TestCase
{
    private PolicyScorer $scorer;

    protected function setUp(): void
    {
        $this->scorer = new PolicyScorer();
    }

    public function testTokenizerDropsStopwordsAndShortTokens(): void
    {
        static::assertSame(['widerrufsfrist', 'bestellung'], $this->scorer->tokenize('Wie ist die Widerrufsfrist für meine Bestellung?'));
        static::assertSame([], $this->scorer->tokenize('wie ist die'));
    }

    public function testRanksTitleHitsAboveBodyHitsAndDropsNonMatches(): void
    {
        $documents = [
            new PolicyDocument('agb', 'AGB', 'footer-navigation', 'Allgemeine Geschäftsbedingungen. Der Widerruf ist in einem eigenen Dokument beschrieben.'),
            new PolicyDocument('widerruf', 'Widerrufsbelehrung', 'footer-navigation', 'Sie können den Widerruf binnen 14 Tagen erklären. Der Widerruf ist formlos möglich.'),
            new PolicyDocument('versand', 'Versand & Zahlung', 'service-navigation', 'Versand mit DHL, 4,90 € pro Bestellung.'),
        ];

        $matches = $this->scorer->rank('Widerruf', $documents, 5);

        static::assertCount(2, $matches);
        static::assertSame('widerruf', $matches[0]->document->policyId);
        static::assertSame('agb', $matches[1]->document->policyId);
        static::assertGreaterThan($matches[1]->score, $matches[0]->score);
    }

    public function testLimitIsApplied(): void
    {
        $documents = [
            new PolicyDocument('a', 'Versand A', 'x', 'Versand'),
            new PolicyDocument('b', 'Versand B', 'x', 'Versand'),
            new PolicyDocument('c', 'Versand C', 'x', 'Versand'),
        ];

        static::assertCount(2, $this->scorer->rank('Versand', $documents, 2));
        static::assertSame([], $this->scorer->rank('Versand', $documents, 0));
    }

    public function testExcerptIsWindowedAroundFirstBodyHitAndCapped(): void
    {
        $filler = str_repeat('Lorem ipsum dolor sit amet. ', 60);
        $content = $filler . 'Die Rücksendung ist innerhalb von 30 Tagen kostenlos.' . $filler;
        $document = new PolicyDocument('ret', 'Service', 'x', $content);

        $matches = $this->scorer->rank('Rücksendung', [$document], 1, 200);

        static::assertCount(1, $matches);
        $excerpt = $matches[0]->excerpt;
        static::assertStringContainsString('Rücksendung', $excerpt);
        static::assertStringStartsWith('…', $excerpt);
        static::assertStringEndsWith('…', $excerpt);
        static::assertLessThanOrEqual(202, mb_strlen($excerpt));
    }

    public function testShortContentIsReturnedUntouched(): void
    {
        $document = new PolicyDocument('kurz', 'Impressum', 'x', 'Beispiel GmbH, Musterstraße 1');

        $matches = $this->scorer->rank('Impressum', [$document], 1);

        static::assertSame('Beispiel GmbH, Musterstraße 1', $matches[0]->excerpt);
        static::assertSame([
            'policy_id' => 'kurz',
            'title' => 'Impressum',
            'category' => 'x',
            'content' => 'Beispiel GmbH, Musterstraße 1',
            'url' => null,
            'score' => $matches[0]->toArray()['score'],
        ], $matches[0]->toArray());
    }
}
