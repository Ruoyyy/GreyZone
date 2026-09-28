<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\StagedChange;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Swag\CommerceAgentTools\StagedChange\ChangeStatus;
use Swag\CommerceAgentTools\StagedChange\StagedChangeException;
use Swag\CommerceAgentTools\StagedChange\StagedChangeStateMachine;

/**
 * @internal
 */
#[CoversClass(StagedChangeStateMachine::class)]
#[CoversClass(StagedChangeException::class)]
#[CoversClass(ChangeStatus::class)]
class StagedChangeStateMachineTest extends TestCase
{
    #[DataProvider('transitionProvider')]
    public function testTransitions(ChangeStatus $from, ChangeStatus $to, bool $allowed): void
    {
        $machine = new StagedChangeStateMachine();

        static::assertSame($allowed, $machine->canTransition($from, $to));

        if ($allowed) {
            $machine->assertTransition('change-1', $from, $to);
            $this->addToAssertionCount(1);

            return;
        }

        try {
            $machine->assertTransition('change-1', $from, $to);
            static::fail('Expected StagedChangeException');
        } catch (StagedChangeException $e) {
            static::assertSame(StagedChangeException::CODE_INVALID_TRANSITION, $e->getCode());
            static::assertStringContainsString('change-1', $e->getMessage());
            static::assertStringContainsString($from->value, $e->getMessage());
            static::assertStringContainsString($to->value, $e->getMessage());
        }
    }

    /**
     * @return iterable<string, array{ChangeStatus, ChangeStatus, bool}>
     */
    public static function transitionProvider(): iterable
    {
        yield 'staged → applied' => [ChangeStatus::Staged, ChangeStatus::Applied, true];
        yield 'staged → discarded' => [ChangeStatus::Staged, ChangeStatus::Discarded, true];
        yield 'staged → staged' => [ChangeStatus::Staged, ChangeStatus::Staged, false];
        yield 'applied → applied (double apply)' => [ChangeStatus::Applied, ChangeStatus::Applied, false];
        yield 'applied → discarded' => [ChangeStatus::Applied, ChangeStatus::Discarded, false];
        yield 'applied → staged (reopen)' => [ChangeStatus::Applied, ChangeStatus::Staged, false];
        yield 'discarded → applied' => [ChangeStatus::Discarded, ChangeStatus::Applied, false];
        yield 'discarded → discarded' => [ChangeStatus::Discarded, ChangeStatus::Discarded, false];
        yield 'discarded → staged' => [ChangeStatus::Discarded, ChangeStatus::Staged, false];
    }

    public function testTerminalStates(): void
    {
        $machine = new StagedChangeStateMachine();

        static::assertFalse($machine->isTerminal(ChangeStatus::Staged));
        static::assertTrue($machine->isTerminal(ChangeStatus::Applied));
        static::assertTrue($machine->isTerminal(ChangeStatus::Discarded));
    }
}
