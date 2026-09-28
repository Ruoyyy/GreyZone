<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Migration;

use Doctrine\DBAL\Connection;
use Shopware\Core\Framework\Migration\MigrationStep;

class Migration1788434320CreateAgentStagedChange extends MigrationStep
{
    public function getCreationTimestamp(): int
    {
        return 1788434320;
    }

    public function update(Connection $connection): void
    {
        $connection->executeStatement(<<<'SQL'
            CREATE TABLE IF NOT EXISTS `swag_agent_staged_change` (
                `id`                BINARY(16)      NOT NULL,
                `kind`              VARCHAR(64)     NOT NULL,
                `status`            VARCHAR(32)     NOT NULL DEFAULT 'staged',
                `summary`           LONGTEXT        NOT NULL,
                `note`              LONGTEXT        NULL,
                `target_entity`     VARCHAR(64)     NOT NULL,
                `items`             JSON            NOT NULL,
                `payload`           JSON            NOT NULL,
                `preview`           JSON            NULL,
                `guardrail_notes`   JSON            NULL,
                `created_by`        VARCHAR(255)    NOT NULL,
                `created_by_kind`   VARCHAR(32)     NOT NULL,
                `applied_by`        VARCHAR(255)    NULL,
                `applied_at`        DATETIME(3)     NULL,
                `discarded_by`      VARCHAR(255)    NULL,
                `discarded_at`      DATETIME(3)     NULL,
                `error_message`     LONGTEXT        NULL,
                `sales_channel_id`  BINARY(16)      NULL,
                `currency`          VARCHAR(3)      NULL,
                `margin_before_pct` DOUBLE          NULL,
                `margin_after_pct`  DOUBLE          NULL,
                `min_margin_pct`    DOUBLE          NULL,
                `created_at`        DATETIME(3)     NOT NULL,
                `updated_at`        DATETIME(3)     NULL,
                PRIMARY KEY (`id`),
                KEY `idx.swag_agent_staged_change.status` (`status`),
                KEY `idx.swag_agent_staged_change.kind` (`kind`),
                KEY `idx.swag_agent_staged_change.created_at` (`created_at`),
                CONSTRAINT `json.swag_agent_staged_change.items` CHECK (JSON_VALID(`items`)),
                CONSTRAINT `json.swag_agent_staged_change.payload` CHECK (JSON_VALID(`payload`)),
                CONSTRAINT `json.swag_agent_staged_change.preview` CHECK (JSON_VALID(`preview`)),
                CONSTRAINT `json.swag_agent_staged_change.guardrail_notes` CHECK (JSON_VALID(`guardrail_notes`)),
                CONSTRAINT `fk.swag_agent_staged_change.sales_channel_id`
                    FOREIGN KEY (`sales_channel_id`) REFERENCES `sales_channel` (`id`)
                    ON DELETE SET NULL ON UPDATE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        SQL);
    }
}
