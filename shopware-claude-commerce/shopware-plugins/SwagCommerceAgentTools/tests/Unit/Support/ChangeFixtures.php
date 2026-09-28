<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Support;

use PHPUnit\Framework\TestCase;
use Shopware\Core\Defaults;
use Shopware\Core\Framework\Api\Context\AdminApiSource;
use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Criteria;
use Shopware\Core\Framework\DataAbstractionLayer\Search\EntitySearchResult;
use Shopware\Core\Framework\Mcp\Context\McpContextProvider;
use Swag\CommerceAgentTools\StagedChange\ChangeStatus;
use Swag\CommerceAgentTools\StagedChange\Entity\AgentStagedChangeCollection;
use Swag\CommerceAgentTools\StagedChange\Entity\AgentStagedChangeDefinition;
use Swag\CommerceAgentTools\StagedChange\Entity\AgentStagedChangeEntity;

/**
 * Shared fixtures for the staged-change tests.
 */
final class ChangeFixtures
{
    public const CHANGE_ID = '0190aaaa000000000000000000000001';
    public const PRODUCT_ID = '0190c1b2d3e4f5a6b7c8d9e0f1a2b3c4';
    public const USER_ID = '0190bbbb000000000000000000000002';
    public const INTEGRATION_ID = '0190cccc000000000000000000000003';

    public static function change(ChangeStatus $status = ChangeStatus::Staged, string $id = self::CHANGE_ID): AgentStagedChangeEntity
    {
        $change = new AgentStagedChangeEntity();
        $change->setId($id);
        $change->setUniqueIdentifier($id);
        $change->setKind('inventory_action');
        $change->setStatus($status->value);
        $change->setSummary('Restock stool by 25');
        $change->setTargetEntity('product');
        $change->setItems([['productId' => self::PRODUCT_ID, 'action' => 'restock', 'quantity' => 25]]);
        $change->setPayload([['id' => self::PRODUCT_ID, 'stock' => 28]]);
        $change->setPreview([['target' => self::PRODUCT_ID, 'targetLabel' => 'Hocker', 'field' => 'stock', 'before' => 3, 'after' => 28]]);
        $change->setCreatedBy(self::INTEGRATION_ID);
        $change->setCreatedByKind('integration');
        $change->setCreatedAt(new \DateTimeImmutable('2026-09-03 10:00:00'));

        return $change;
    }

    /**
     * @param list<string> $privileges
     */
    public static function adminContext(array $privileges, ?string $userId = null, ?string $integrationId = self::INTEGRATION_ID): Context
    {
        $source = new AdminApiSource($userId, $integrationId);
        $source->setPermissions($privileges);

        return new Context($source, [], Defaults::CURRENCY, [Defaults::LANGUAGE_SYSTEM]);
    }

    public static function contextProvider(TestCase $test, Context $context): McpContextProvider
    {
        return new FixedContextProvider($context);
    }

    /**
     * @return EntitySearchResult<AgentStagedChangeCollection>
     */
    public static function searchResult(Context $context, AgentStagedChangeEntity ...$changes): EntitySearchResult
    {
        return new EntitySearchResult(
            AgentStagedChangeDefinition::ENTITY_NAME,
            \count($changes),
            new AgentStagedChangeCollection($changes),
            null,
            new Criteria(),
            $context,
        );
    }
}
