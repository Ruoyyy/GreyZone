<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

enum ChangeStatus: string
{
    case Staged = 'staged';
    case Applied = 'applied';
    case Discarded = 'discarded';

    /**
     * @return list<string>
     */
    public static function values(): array
    {
        return array_column(self::cases(), 'value');
    }
}
