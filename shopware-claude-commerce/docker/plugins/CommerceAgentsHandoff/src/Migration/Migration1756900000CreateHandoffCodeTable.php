<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff\Migration;

use CommerceAgents\Handoff\CommerceAgentsHandoff;
use Doctrine\DBAL\Connection;
use Shopware\Core\Framework\Migration\MigrationStep;

/**
 * Single-use store for consumed handoff codes: one row per accepted `jti`, purged once
 * `expires_at` has passed (the code itself is rejected as expired from then on, so the
 * row is no longer needed for replay protection).
 */
class Migration1756900000CreateHandoffCodeTable extends MigrationStep
{
    public function getCreationTimestamp(): int
    {
        return 1756900000;
    }

    public function update(Connection $connection): void
    {
        $connection->executeStatement(
            'CREATE TABLE IF NOT EXISTS `' . CommerceAgentsHandoff::CONSUMED_CODE_TABLE . '` (
                `jti` BINARY(16) NOT NULL,
                `expires_at` DATETIME(3) NOT NULL,
                PRIMARY KEY (`jti`),
                KEY `idx.commerce_agents_handoff_code.expires_at` (`expires_at`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci'
        );
    }
}
