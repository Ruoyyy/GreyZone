<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff\Service;

/**
 * The code is malformed, forged, expired, already used, or does not carry a context
 * token. The message is for the log only — the controller shows a generic flash.
 */
final class HandoffCodeException extends \RuntimeException
{
    public static function malformed(string $detail): self
    {
        return new self('handoff code malformed: ' . $detail);
    }

    public static function signatureMismatch(): self
    {
        return new self('handoff code signature mismatch');
    }

    public static function unsupportedVersion(): self
    {
        return new self('handoff code has an unsupported version');
    }

    public static function lifetimeOutOfRange(): self
    {
        return new self('handoff code lifetime out of range');
    }

    public static function expired(): self
    {
        return new self('handoff code expired');
    }

    public static function issuedInFuture(): self
    {
        return new self('handoff code issued in the future');
    }

    public static function alreadyUsed(): self
    {
        return new self('handoff code already used');
    }

    public static function tokenBoxInvalid(): self
    {
        return new self('handoff token box does not authenticate');
    }

    public static function notAContextToken(): self
    {
        return new self('decrypted value is not a context token');
    }
}
