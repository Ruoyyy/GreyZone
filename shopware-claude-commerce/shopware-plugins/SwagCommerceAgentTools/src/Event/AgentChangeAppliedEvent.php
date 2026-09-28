<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Event;

/**
 * Fired after a staged change has been written to the live catalog.
 */
class AgentChangeAppliedEvent extends AbstractAgentChangeEvent
{
    public const EVENT_NAME = 'swag.agent.change.applied';

    public function getName(): string
    {
        return self::EVENT_NAME;
    }
}
