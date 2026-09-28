<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Analytics;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Swag\CommerceAgentTools\Analytics\ReportingPeriod;

/**
 * @internal
 */
#[CoversClass(ReportingPeriod::class)]
class ReportingPeriodTest extends TestCase
{
    private \DateTimeImmutable $now;

    protected function setUp(): void
    {
        // Thursday, 3 September 2026, 13:11 Europe/Berlin
        $this->now = new \DateTimeImmutable('2026-09-03 13:11:00', new \DateTimeZone('Europe/Berlin'));
    }

    /**
     * @param array{string, string, string, string, int} $expected from, to, previousFrom, previousTo, days
     */
    #[DataProvider('periodProvider')]
    public function testParse(string $token, array $expected): void
    {
        $period = ReportingPeriod::parse($token, $this->now);

        static::assertSame($expected[0], $period->from->format('Y-m-d H:i'), 'from');
        static::assertSame($expected[1], $period->to->format('Y-m-d H:i'), 'to');
        static::assertSame($expected[2], $period->previousFrom->format('Y-m-d H:i'), 'previousFrom');
        static::assertSame($expected[3], $period->previousTo->format('Y-m-d H:i'), 'previousTo');
        static::assertSame($expected[4], $period->days(), 'days');
        static::assertSame('Europe/Berlin', $period->from->getTimezone()->getName());
    }

    /**
     * @return iterable<string, array{string, array{string, string, string, string, int}}>
     */
    public static function periodProvider(): iterable
    {
        yield '30d (default)' => ['30d', ['2026-08-05 00:00', '2026-09-04 00:00', '2026-07-06 00:00', '2026-08-05 00:00', 30]];
        yield 'empty → default' => ['', ['2026-08-05 00:00', '2026-09-04 00:00', '2026-07-06 00:00', '2026-08-05 00:00', 30]];
        yield '7d' => ['7d', ['2026-08-28 00:00', '2026-09-04 00:00', '2026-08-21 00:00', '2026-08-28 00:00', 7]];
        yield 'today' => ['today', ['2026-09-03 00:00', '2026-09-04 00:00', '2026-09-02 00:00', '2026-09-03 00:00', 1]];
        yield 'yesterday' => ['yesterday', ['2026-09-02 00:00', '2026-09-03 00:00', '2026-09-01 00:00', '2026-09-02 00:00', 1]];
        yield 'this_week (Mon-Sun)' => ['this_week', ['2026-08-31 00:00', '2026-09-07 00:00', '2026-08-24 00:00', '2026-08-31 00:00', 7]];
        yield 'last_week' => ['last_week', ['2026-08-24 00:00', '2026-08-31 00:00', '2026-08-17 00:00', '2026-08-24 00:00', 7]];
        yield 'this_month' => ['this_month', ['2026-09-01 00:00', '2026-10-01 00:00', '2026-08-01 00:00', '2026-09-01 00:00', 30]];
        yield 'last_month' => ['last_month', ['2026-08-01 00:00', '2026-09-01 00:00', '2026-07-01 00:00', '2026-08-01 00:00', 31]];
        yield 'this_quarter' => ['this_quarter', ['2026-07-01 00:00', '2026-10-01 00:00', '2026-04-01 00:00', '2026-07-01 00:00', 92]];
        yield 'last_quarter' => ['last_quarter', ['2026-04-01 00:00', '2026-07-01 00:00', '2026-01-01 00:00', '2026-04-01 00:00', 91]];
        yield 'this_year' => ['this_year', ['2026-01-01 00:00', '2027-01-01 00:00', '2025-01-01 00:00', '2026-01-01 00:00', 365]];
        yield 'last_year' => ['last_year', ['2025-01-01 00:00', '2026-01-01 00:00', '2024-01-01 00:00', '2025-01-01 00:00', 365]];
        yield 'ytd compares with same span last year' => ['ytd', ['2026-01-01 00:00', '2026-09-04 00:00', '2025-01-01 00:00', '2025-09-04 00:00', 246]];
        yield 'explicit range' => ['2026-08-01..2026-08-31', ['2026-08-01 00:00', '2026-09-01 00:00', '2026-07-01 00:00', '2026-08-01 00:00', 31]];
        yield 'explicit range with slash' => ['2026-08-01/2026-08-01', ['2026-08-01 00:00', '2026-08-02 00:00', '2026-07-31 00:00', '2026-08-01 00:00', 1]];
        yield 'case insensitive' => ['Last_Month', ['2026-08-01 00:00', '2026-09-01 00:00', '2026-07-01 00:00', '2026-08-01 00:00', 31]];
    }

    #[DataProvider('invalidPeriodProvider')]
    public function testInvalidPeriods(string $token, string $messagePart): void
    {
        $this->expectException(\InvalidArgumentException::class);
        $this->expectExceptionMessageMatches('/' . preg_quote($messagePart, '/') . '/');

        ReportingPeriod::parse($token, $this->now);
    }

    /**
     * @return iterable<string, array{string, string}>
     */
    public static function invalidPeriodProvider(): iterable
    {
        yield 'unknown token' => ['fortnight', 'Unknown period "fortnight"'];
        yield 'zero days' => ['0d', 'between 1d and 730d'];
        yield 'too many days' => ['999d', 'between 1d and 730d'];
        yield 'end before start' => ['2026-08-31..2026-08-01', 'must not be before'];
        yield 'invalid date' => ['2026-02-30..2026-03-01', 'not a valid calendar date'];
        yield 'too long explicit' => ['2020-01-01..2026-01-01', 'at most 730 days'];
    }

    public function testDeltaPct(): void
    {
        static::assertSame(25.0, ReportingPeriod::deltaPct(125.0, 100.0));
        static::assertSame(-50.0, ReportingPeriod::deltaPct(50.0, 100.0));
        static::assertSame(0.0, ReportingPeriod::deltaPct(100.0, 100.0));
        static::assertNull(ReportingPeriod::deltaPct(10.0, 0.0));
        static::assertSame(33.33, ReportingPeriod::deltaPct(4.0, 3.0));
    }

    public function testToArrayUsesAtomTimestamps(): void
    {
        $period = ReportingPeriod::parse('7d', $this->now)->toArray();

        static::assertSame('7d', $period['label']);
        static::assertSame('2026-08-28T00:00:00+02:00', $period['from']);
        static::assertSame('2026-09-04T00:00:00+02:00', $period['to']);
        static::assertSame(7, $period['days']);
    }
}
