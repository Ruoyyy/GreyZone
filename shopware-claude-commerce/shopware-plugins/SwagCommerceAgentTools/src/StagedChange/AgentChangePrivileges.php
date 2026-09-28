<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

use Swag\CommerceAgentTools\StagedChange\Entity\AgentStagedChangeDefinition;

/**
 * ACL privileges of the staged-change tools.
 *
 * `agent_change:*` is the workflow privilege the tools check explicitly; the
 * `swag_agent_staged_change:*` privileges are what the DAL enforces on the
 * ledger rows. The role template in Resources/config/acl-role-template.json
 * grants both together, so a role built from it works end to end.
 */
final class AgentChangePrivileges
{
    public const WORKFLOW_READ = 'agent_change:read';
    public const WORKFLOW_CREATE = 'agent_change:create';
    public const WORKFLOW_UPDATE = 'agent_change:update';

    public const LEDGER_READ = AgentStagedChangeDefinition::ENTITY_NAME . ':read';
    public const LEDGER_CREATE = AgentStagedChangeDefinition::ENTITY_NAME . ':create';
    public const LEDGER_UPDATE = AgentStagedChangeDefinition::ENTITY_NAME . ':update';

    /**
     * @return list<string>
     */
    public static function forList(): array
    {
        return [self::WORKFLOW_READ, self::LEDGER_READ];
    }

    /**
     * @return list<string>
     */
    public static function forStage(): array
    {
        return [self::WORKFLOW_CREATE, self::LEDGER_CREATE, self::LEDGER_READ];
    }

    /**
     * @return list<string>
     */
    public static function forTransition(): array
    {
        return [self::WORKFLOW_UPDATE, self::LEDGER_UPDATE, self::LEDGER_READ];
    }
}
