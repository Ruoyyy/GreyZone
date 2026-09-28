<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange\Entity;

use Shopware\Core\Framework\DataAbstractionLayer\EntityCollection;

/**
 * @extends EntityCollection<AgentStagedChangeEntity>
 */
class AgentStagedChangeCollection extends EntityCollection
{
    protected function getExpectedClass(): string
    {
        return AgentStagedChangeEntity::class;
    }
}
