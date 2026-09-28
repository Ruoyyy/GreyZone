<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff;

use Doctrine\DBAL\Connection;
use Shopware\Core\Framework\Plugin;
use Shopware\Core\Framework\Plugin\Context\UninstallContext;

/**
 * Claude Commerce checkout handoff (ADR-10).
 *
 * The shopping agent's UCP cart is a Store API context token. The host never puts
 * that token into a URL; it mints a one-time, HMAC-signed, AES-GCM-encrypted handoff
 * code (shopware_common/handoff.py) and the shopper's browser POSTs it to
 * /claude-commerce/continue. The plugin verifies the code, adopts the token into a
 * fresh storefront session and redirects to /checkout/confirm.
 */
class CommerceAgentsHandoff extends Plugin
{
    public const CONSUMED_CODE_TABLE = 'commerce_agents_handoff_code';

    public function uninstall(UninstallContext $uninstallContext): void
    {
        parent::uninstall($uninstallContext);

        if ($uninstallContext->keepUserData()) {
            return;
        }

        /** @var Connection $connection */
        $connection = $this->container->get(Connection::class);
        $connection->executeStatement('DROP TABLE IF EXISTS `' . self::CONSUMED_CODE_TABLE . '`');
    }
}
