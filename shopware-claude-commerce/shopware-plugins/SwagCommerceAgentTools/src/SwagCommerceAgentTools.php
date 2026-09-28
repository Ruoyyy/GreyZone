<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools;

use Doctrine\DBAL\Connection;
use Shopware\Core\Framework\Plugin;
use Shopware\Core\Framework\Plugin\Context\UninstallContext;
use Swag\CommerceAgentTools\StagedChange\Entity\AgentStagedChangeDefinition;

/**
 * Agent Tool API for Shopware 6.7.
 *
 * Registers Store API MCP tools (group `agent-shopping`), Admin API MCP tools
 * (group `agent-merchant`), the `swag_agent_staged_change` entity and the
 * Flow Builder business events `swag.agent.change.staged` / `swag.agent.change.applied`.
 */
class SwagCommerceAgentTools extends Plugin
{
    public function uninstall(UninstallContext $uninstallContext): void
    {
        parent::uninstall($uninstallContext);

        if ($uninstallContext->keepUserData()) {
            return;
        }

        $connection = $this->container?->get(Connection::class);
        if (!$connection instanceof Connection) {
            return;
        }

        $connection->executeStatement(\sprintf('DROP TABLE IF EXISTS `%s`', AgentStagedChangeDefinition::ENTITY_NAME));
    }
}
