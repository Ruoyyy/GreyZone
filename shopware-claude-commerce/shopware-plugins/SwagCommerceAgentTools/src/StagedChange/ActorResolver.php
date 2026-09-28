<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

use Shopware\Core\Framework\Api\Context\AdminApiSource;
use Shopware\Core\Framework\Context;

/**
 * Derives "who did this" from the API context source. The MCP endpoint is
 * authenticated as an integration (client credentials) or as an admin user,
 * so the actor is one of those; anything else is recorded as system.
 */
class ActorResolver
{
    public const KIND_USER = 'user';
    public const KIND_INTEGRATION = 'integration';
    public const KIND_SYSTEM = 'system';

    /**
     * @return array{id: string, kind: string}
     */
    public function resolve(Context $context): array
    {
        $source = $context->getSource();

        if ($source instanceof AdminApiSource) {
            if ($source->getUserId() !== null) {
                return ['id' => $source->getUserId(), 'kind' => self::KIND_USER];
            }
            if ($source->getIntegrationId() !== null) {
                return ['id' => $source->getIntegrationId(), 'kind' => self::KIND_INTEGRATION];
            }
        }

        return ['id' => self::KIND_SYSTEM, 'kind' => self::KIND_SYSTEM];
    }
}
