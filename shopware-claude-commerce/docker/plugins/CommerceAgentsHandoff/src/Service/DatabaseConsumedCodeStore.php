<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff\Service;

use CommerceAgents\Handoff\CommerceAgentsHandoff;
use Doctrine\DBAL\Connection;
use Doctrine\DBAL\Exception\UniqueConstraintViolationException;
use Shopware\Core\Defaults;

/**
 * `commerce_agents_handoff_code (jti BINARY(16) PK, expires_at DATETIME(3))`, created by
 * {@see \CommerceAgents\Handoff\Migration\Migration1756900000CreateHandoffCodeTable}.
 * The primary key makes "first insert wins" hold across concurrent PHP workers.
 */
final class DatabaseConsumedCodeStore implements ConsumedCodeStore
{
    public function __construct(private readonly Connection $connection)
    {
    }

    public function consume(string $jti, int $expiresAt, int $now): bool
    {
        $table = CommerceAgentsHandoff::CONSUMED_CODE_TABLE;

        $this->connection->executeStatement(
            sprintf('DELETE FROM `%s` WHERE `expires_at` < :now', $table),
            ['now' => self::formatTimestamp($now)]
        );

        try {
            $this->connection->insert($table, [
                'jti' => hex2bin($jti),
                'expires_at' => self::formatTimestamp($expiresAt),
            ]);
        } catch (UniqueConstraintViolationException) {
            return false;
        }

        return true;
    }

    private static function formatTimestamp(int $timestamp): string
    {
        return (new \DateTimeImmutable('@' . $timestamp))->format(Defaults::STORAGE_DATE_TIME_FORMAT);
    }
}
