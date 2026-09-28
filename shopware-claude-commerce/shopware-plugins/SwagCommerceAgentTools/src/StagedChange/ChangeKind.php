<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

/**
 * Change kinds mirror the Blueprint's `stage_*` methods of MerchantBackend.
 */
enum ChangeKind: string
{
    case ListingUpdate = 'listing_update';
    case PriceUpdate = 'price_update';
    case InventoryAction = 'inventory_action';
    case Promotion = 'promotion';
    case Campaign = 'campaign';

    /**
     * Kinds this increment can plan and apply. Promotions (Dynamic Product Groups,
     * Rule Builder) and campaigns are deferred; staging them is refused with a
     * message the agent can relay.
     */
    public function isSupported(): bool
    {
        return match ($this) {
            self::ListingUpdate, self::PriceUpdate, self::InventoryAction => true,
            self::Promotion, self::Campaign => false,
        };
    }

    /**
     * @return list<string>
     */
    public static function values(): array
    {
        return array_column(self::cases(), 'value');
    }

    /**
     * @return list<string>
     */
    public static function supportedValues(): array
    {
        return array_values(array_filter(self::values(), static fn (string $value): bool => self::from($value)->isSupported()));
    }
}
