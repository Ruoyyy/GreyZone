<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Analytics;

/**
 * Resolves the agent's period vocabulary ("30d", "last_month", "2026-08-01..2026-08-31")
 * into a half-open date range [from, to) plus the comparison period before it.
 *
 * Calendar periods compare with the previous calendar unit (this month vs. last
 * month); rolling periods compare with the same number of days directly before.
 */
final class ReportingPeriod
{
    public const DEFAULT = '30d';
    public const MAX_ROLLING_DAYS = 730;

    private const CALENDAR_TOKENS = [
        'today', 'yesterday', 'this_week', 'last_week', 'this_month', 'last_month',
        'this_quarter', 'last_quarter', 'this_year', 'last_year', 'ytd',
    ];

    private function __construct(
        public readonly string $label,
        public readonly \DateTimeImmutable $from,
        public readonly \DateTimeImmutable $to,
        public readonly \DateTimeImmutable $previousFrom,
        public readonly \DateTimeImmutable $previousTo,
    ) {
    }

    /**
     * @throws \InvalidArgumentException with an agent-readable message
     */
    public static function parse(string $period, \DateTimeImmutable $now): self
    {
        $token = strtolower(trim($period));
        if ($token === '') {
            $token = self::DEFAULT;
        }

        $today = $now->setTime(0, 0);

        if (preg_match('/^(\d{1,3})d$/', $token, $matches) === 1) {
            $days = (int) $matches[1];
            if ($days < 1 || $days > self::MAX_ROLLING_DAYS) {
                throw new \InvalidArgumentException(\sprintf('Rolling periods must be between 1d and %dd.', self::MAX_ROLLING_DAYS));
            }
            $to = $today->modify('+1 day');
            $from = $to->modify(\sprintf('-%d days', $days));

            return self::rolling($token, $from, $to);
        }

        if (preg_match('/^(\d{4}-\d{2}-\d{2})(?:\.\.|\/)(\d{4}-\d{2}-\d{2})$/', $token, $matches) === 1) {
            $from = self::parseDate($matches[1], $now->getTimezone());
            $toInclusive = self::parseDate($matches[2], $now->getTimezone());
            if ($toInclusive < $from) {
                throw new \InvalidArgumentException('The end date of an explicit period must not be before the start date.');
            }
            $to = $toInclusive->modify('+1 day');
            if ($from->diff($to)->days > self::MAX_ROLLING_DAYS) {
                throw new \InvalidArgumentException(\sprintf('Explicit periods may span at most %d days.', self::MAX_ROLLING_DAYS));
            }

            return self::rolling($token, $from, $to);
        }

        return match ($token) {
            'today' => self::rolling($token, $today, $today->modify('+1 day')),
            'yesterday' => self::rolling($token, $today->modify('-1 day'), $today),
            'this_week' => self::calendar($token, self::startOfWeek($today), self::startOfWeek($today)->modify('+1 week'), '1 week'),
            'last_week' => self::calendar($token, self::startOfWeek($today)->modify('-1 week'), self::startOfWeek($today), '1 week'),
            'this_month' => self::calendar($token, $today->modify('first day of this month'), $today->modify('first day of next month'), '1 month'),
            'last_month' => self::calendar($token, $today->modify('first day of last month'), $today->modify('first day of this month'), '1 month'),
            'this_quarter' => self::calendar($token, self::startOfQuarter($today), self::startOfQuarter($today)->modify('+3 months'), '3 months'),
            'last_quarter' => self::calendar($token, self::startOfQuarter($today)->modify('-3 months'), self::startOfQuarter($today), '3 months'),
            'this_year' => self::calendar($token, $today->modify('first day of january this year'), $today->modify('first day of january next year'), '1 year'),
            'last_year' => self::calendar($token, $today->modify('first day of january last year'), $today->modify('first day of january this year'), '1 year'),
            'ytd' => self::calendar($token, $today->modify('first day of january this year'), $today->modify('+1 day'), '1 year'),
            default => throw new \InvalidArgumentException(\sprintf(
                'Unknown period "%s". Use a rolling period like 7d/30d/90d, one of %s, or an explicit range YYYY-MM-DD..YYYY-MM-DD.',
                $period,
                implode(', ', self::CALENDAR_TOKENS),
            )),
        };
    }

    public function days(): int
    {
        return (int) $this->from->diff($this->to)->days;
    }

    /**
     * @return array{label: string, from: string, to: string, previousFrom: string, previousTo: string, days: int}
     */
    public function toArray(): array
    {
        return [
            'label' => $this->label,
            'from' => $this->from->format(\DATE_ATOM),
            'to' => $this->to->format(\DATE_ATOM),
            'previousFrom' => $this->previousFrom->format(\DATE_ATOM),
            'previousTo' => $this->previousTo->format(\DATE_ATOM),
            'days' => $this->days(),
        ];
    }

    /**
     * Percent change from the previous to the current value; null when there is no baseline.
     */
    public static function deltaPct(float $current, float $previous): ?float
    {
        if (abs($previous) < 1e-9) {
            return null;
        }

        return round(($current - $previous) / abs($previous) * 100, 2);
    }

    private static function rolling(string $label, \DateTimeImmutable $from, \DateTimeImmutable $to): self
    {
        $length = $from->diff($to);
        $previousTo = $from;
        $previousFrom = $from->sub($length);

        return new self($label, $from, $to, $previousFrom, $previousTo);
    }

    private static function calendar(string $label, \DateTimeImmutable $from, \DateTimeImmutable $to, string $unit): self
    {
        $previousFrom = $from->modify('-' . $unit);
        // For "ytd" the comparison window is the same day-count in the previous year.
        $previousTo = $label === 'ytd' ? $previousFrom->add($from->diff($to)) : $from;

        return new self($label, $from, $to, $previousFrom, $previousTo);
    }

    private static function startOfWeek(\DateTimeImmutable $day): \DateTimeImmutable
    {
        $weekday = (int) $day->format('N');

        return $day->modify(\sprintf('-%d days', $weekday - 1));
    }

    private static function startOfQuarter(\DateTimeImmutable $day): \DateTimeImmutable
    {
        $month = (int) $day->format('n');
        $quarterStartMonth = (int) (floor(($month - 1) / 3) * 3) + 1;

        return $day->setDate((int) $day->format('Y'), $quarterStartMonth, 1);
    }

    private static function parseDate(string $date, \DateTimeZone $timezone): \DateTimeImmutable
    {
        $parsed = \DateTimeImmutable::createFromFormat('!Y-m-d', $date, $timezone);
        if ($parsed === false || $parsed->format('Y-m-d') !== $date) {
            throw new \InvalidArgumentException(\sprintf('"%s" is not a valid calendar date (YYYY-MM-DD).', $date));
        }

        return $parsed;
    }
}
