<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

/**
 * staged → applied, staged → discarded. Everything else is refused.
 * Applied and discarded are terminal: a change that has been written to the
 * shop or rejected by an approver is never reopened, a new change is staged.
 */
class StagedChangeStateMachine
{
    /** @var array<string, list<string>> */
    private const TRANSITIONS = [
        ChangeStatus::Staged->value => [ChangeStatus::Applied->value, ChangeStatus::Discarded->value],
        ChangeStatus::Applied->value => [],
        ChangeStatus::Discarded->value => [],
    ];

    public function canTransition(ChangeStatus $from, ChangeStatus $to): bool
    {
        return \in_array($to->value, self::TRANSITIONS[$from->value] ?? [], true);
    }

    /**
     * @throws StagedChangeException
     */
    public function assertTransition(string $changeId, ChangeStatus $from, ChangeStatus $to): void
    {
        if (!$this->canTransition($from, $to)) {
            throw StagedChangeException::invalidTransition($changeId, $from->value, $to->value);
        }
    }

    public function isTerminal(ChangeStatus $status): bool
    {
        return (self::TRANSITIONS[$status->value] ?? []) === [];
    }
}
