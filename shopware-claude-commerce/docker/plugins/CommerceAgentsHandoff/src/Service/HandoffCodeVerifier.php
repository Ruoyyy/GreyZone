<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff\Service;

use Psr\Clock\ClockInterface;

/**
 * PHP mirror of `shopware_common.handoff.HandoffCodeVerifier.verify` (ADR-10).
 *
 * Wire format (v=1):
 *
 *   code     = b64url(payload) "." b64url(HMAC-SHA256(mac_key, payload))
 *   payload  = compact JSON, keys sorted: {"exp","iat","jti","tok","v"}
 *   box      = nonce(12) || AES-256-GCM(enc_key, nonce, aad=jti, token) || tag(16)
 *   enc_key  = HMAC-SHA256(secret, "commerce-agents-handoff:enc")
 *   mac_key  = HMAC-SHA256(secret, "commerce-agents-handoff:mac")
 *
 * PHP's `hash_hmac($algo, $data, $key)` takes the *data* second and the *key* third, so
 * the info string is the data and the shared secret is the key — the same as Python's
 * `hmac.new(secret, info, sha256)`.
 *
 * Checks run in the same order as the Python reference; the `jti` is only marked as
 * consumed after the token box authenticated, so a forged box never burns a code id.
 */
final class HandoffCodeVerifier
{
    public const VERSION = 1;
    public const MAX_TTL_SECONDS = 120;
    public const CLOCK_SKEW_SECONDS = 60;
    public const MIN_SECRET_BYTES = 32;
    public const TOKEN_PATTERN = '/^[A-Za-z0-9_-]{16,128}$/';

    private const ENC_INFO = 'commerce-agents-handoff:enc';
    private const MAC_INFO = 'commerce-agents-handoff:mac';
    private const CIPHER = 'aes-256-gcm';
    private const NONCE_BYTES = 12;
    private const TAG_BYTES = 16;
    private const JTI_PATTERN = '/^[0-9a-f]{32}$/';
    private const JSON_MAX_DEPTH = 4;

    private readonly string $encKey;

    private readonly string $macKey;

    public function __construct(
        #[\SensitiveParameter]
        string $secret,
        private readonly ClockInterface $clock,
        private readonly ConsumedCodeStore $consumed,
    ) {
        if (\strlen($secret) < self::MIN_SECRET_BYTES) {
            throw HandoffConfigurationException::secretTooShort(self::MIN_SECRET_BYTES);
        }

        ['enc' => $this->encKey, 'mac' => $this->macKey] = self::deriveKeys($secret);
    }

    /**
     * @return array{enc: string, mac: string} raw 32-byte keys
     */
    public static function deriveKeys(#[\SensitiveParameter] string $secret): array
    {
        return [
            'enc' => hash_hmac('sha256', self::ENC_INFO, $secret, true),
            'mac' => hash_hmac('sha256', self::MAC_INFO, $secret, true),
        ];
    }

    /**
     * @return string the Store API context token (`sw-context-token`)
     *
     * @throws HandoffCodeException
     */
    public function verify(string $code): string
    {
        $now = $this->clock->now()->getTimestamp();

        $parts = explode('.', $code, 2);
        if (\count($parts) !== 2 || $parts[0] === '' || $parts[1] === '') {
            throw HandoffCodeException::malformed('code must be <payload>.<mac>');
        }
        [$payloadB64, $macB64] = $parts;

        $payload = self::base64UrlDecode($payloadB64);
        $mac = self::base64UrlDecode($macB64);
        if ($payload === null || $mac === null) {
            throw HandoffCodeException::malformed('code is not base64url');
        }

        $expected = hash_hmac('sha256', $payload, $this->macKey, true);
        if (!hash_equals($expected, $mac)) {
            throw HandoffCodeException::signatureMismatch();
        }

        try {
            $fields = json_decode($payload, true, self::JSON_MAX_DEPTH, \JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            throw HandoffCodeException::malformed('payload is not JSON');
        }
        if (!\is_array($fields) || ($fields['v'] ?? null) !== self::VERSION) {
            throw HandoffCodeException::unsupportedVersion();
        }

        $issuedAt = self::intField($fields, 'iat');
        $expiresAt = self::intField($fields, 'exp');
        $jti = $fields['jti'] ?? null;
        $boxB64 = $fields['tok'] ?? null;
        if (!\is_string($jti) || !\is_string($boxB64)) {
            throw HandoffCodeException::malformed('payload is missing fields');
        }

        if ($expiresAt - $issuedAt > self::MAX_TTL_SECONDS || $expiresAt <= $issuedAt) {
            throw HandoffCodeException::lifetimeOutOfRange();
        }
        if ($expiresAt < $now) {
            throw HandoffCodeException::expired();
        }
        if ($issuedAt > $now + self::CLOCK_SKEW_SECONDS) {
            throw HandoffCodeException::issuedInFuture();
        }
        if (preg_match(self::JTI_PATTERN, $jti) !== 1) {
            throw HandoffCodeException::malformed('jti malformed');
        }

        $box = self::base64UrlDecode($boxB64);
        if ($box === null) {
            throw HandoffCodeException::malformed('token box is not base64url');
        }
        if (\strlen($box) < self::NONCE_BYTES + self::TAG_BYTES + 1) {
            throw HandoffCodeException::malformed('token box too short');
        }

        $nonce = substr($box, 0, self::NONCE_BYTES);
        $tag = substr($box, -self::TAG_BYTES);
        $ciphertext = substr($box, self::NONCE_BYTES, -self::TAG_BYTES);
        $token = openssl_decrypt(
            $ciphertext,
            self::CIPHER,
            $this->encKey,
            \OPENSSL_RAW_DATA,
            $nonce,
            $tag,
            $jti
        );
        if ($token === false) {
            throw HandoffCodeException::tokenBoxInvalid();
        }
        if (preg_match(self::TOKEN_PATTERN, $token) !== 1) {
            throw HandoffCodeException::notAContextToken();
        }

        if (!$this->consumed->consume($jti, $expiresAt, $now)) {
            throw HandoffCodeException::alreadyUsed();
        }

        return $token;
    }

    /**
     * @param array<mixed> $fields
     */
    private static function intField(array $fields, string $key): int
    {
        $value = $fields[$key] ?? null;
        if (\is_int($value)) {
            return $value;
        }
        if (\is_string($value) && preg_match('/^-?\d+$/', $value) === 1) {
            return (int) $value;
        }
        if (\is_float($value) && floor($value) === $value) {
            return (int) $value;
        }

        throw HandoffCodeException::malformed('payload is missing fields');
    }

    private static function base64UrlDecode(string $text): ?string
    {
        if (preg_match('/^[A-Za-z0-9_-]*$/', $text) !== 1) {
            return null;
        }
        $padded = strtr($text, '-_', '+/') . str_repeat('=', (4 - \strlen($text) % 4) % 4);
        $decoded = base64_decode($padded, true);

        return $decoded === false ? null : $decoded;
    }
}
