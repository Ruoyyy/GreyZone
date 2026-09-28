<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange;

/**
 * Business errors of the staged-change workflow. Messages are written for the
 * agent: they say what went wrong and what to do next.
 */
final class StagedChangeException extends \RuntimeException
{
    public const CODE_INVALID_TRANSITION = 1;
    public const CODE_UNKNOWN_KIND = 2;
    public const CODE_KIND_NOT_SUPPORTED = 3;
    public const CODE_INVALID_ITEMS = 4;
    public const CODE_NOT_FOUND = 5;
    public const CODE_PRODUCTS_MISSING = 6;
    public const CODE_TOO_MANY_ITEMS = 7;

    public static function invalidTransition(string $changeId, string $from, string $to): self
    {
        return new self(
            \sprintf('Change "%s" is "%s" and cannot become "%s". Only staged changes can be applied or discarded; stage a new change instead.', $changeId, $from, $to),
            self::CODE_INVALID_TRANSITION,
        );
    }

    public static function unknownKind(string $kind): self
    {
        return new self(
            \sprintf('Unknown change kind "%s". Allowed: %s.', $kind, implode(', ', ChangeKind::values())),
            self::CODE_UNKNOWN_KIND,
        );
    }

    public static function kindNotSupported(ChangeKind $kind): self
    {
        return new self(
            \sprintf('Change kind "%s" is not supported by this Shopware installation yet (promotion and campaign tooling are planned). Supported: %s.', $kind->value, implode(', ', ChangeKind::supportedValues())),
            self::CODE_KIND_NOT_SUPPORTED,
        );
    }

    public static function invalidItems(string $reason): self
    {
        return new self('Invalid "items": ' . $reason, self::CODE_INVALID_ITEMS);
    }

    public static function notFound(string $changeId): self
    {
        return new self(
            \sprintf('Staged change "%s" was not found. Use agent-change-list to see the open changes.', $changeId),
            self::CODE_NOT_FOUND,
        );
    }

    /**
     * @param list<string> $productIds
     */
    public static function productsMissing(array $productIds): self
    {
        return new self(
            \sprintf('These products do not exist: %s. Use IDs from a previous listing search.', implode(', ', $productIds)),
            self::CODE_PRODUCTS_MISSING,
        );
    }

    public static function tooManyItems(int $count, int $max): self
    {
        return new self(
            \sprintf('A change may contain at most %d items (got %d). Split it into several changes.', $max, $count),
            self::CODE_TOO_MANY_ITEMS,
        );
    }
}
