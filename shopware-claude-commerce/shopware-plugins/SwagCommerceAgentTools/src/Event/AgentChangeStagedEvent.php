<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Event;

/**
 * Fired when a merchant agent records a new staged change (nothing has been
 * written to the live catalog yet). Typical flow: notify the approver.
 */
class AgentChangeStagedEvent extends AbstractAgentChangeEvent
{
    public const EVENT_NAME = 'swag.agent.change.staged';

    public function getName(): string
    {
        return self::EVENT_NAME;
    }
}
