<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff\Tests;

use CommerceAgents\Handoff\Service\HandoffCodeException;
use CommerceAgents\Handoff\Service\HandoffCodeVerifier;
use CommerceAgents\Handoff\Service\HandoffConfigurationException;
use CommerceAgents\Handoff\Service\InMemoryConsumedCodeStore;
use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\TestCase;
use Symfony\Component\Clock\MockClock;

/**
 * Pins the PHP verifier to the Python reference (shopware_common/handoff.py). The fixture
 * code below was minted with:
 *
 *   PYTHONPATH=. python -c "from shopware_common.handoff import HandoffCodeIssuer; \
 *     print(HandoffCodeIssuer('0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef') \
 *     .issue('SWSCVGF5ZUJQBWJ0QMP1OXPZNQ', now=1800000000).code)"
 */
#[CoversClass(HandoffCodeVerifier::class)]
#[CoversClass(InMemoryConsumedCodeStore::class)]
final class HandoffCodeVerifierTest extends TestCase
{
    private const SECRET = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
    private const OTHER_SECRET = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff';
    private const TOKEN = 'SWSCVGF5ZUJQBWJ0QMP1OXPZNQ';
    private const ISSUED_AT = 1800000000;
    private const NOW = 1800000010;

    private const PYTHON_CODE = 'eyJleHAiOjE4MDAwMDAxMjAsImlhdCI6MTgwMDAwMDAwMCwianRpIjoiNDYzMWY1NDYxMDdhNmM5ZGJhNDEyNzgzMDA0ODc5ZjgiLCJ0b2siOiJRRFNLX0diMmFDeTFFUzYwSC1Lckc5aTZkS0RXdGN2VFlvU2pCaWlmOUxMeVctMDRRMVRJREwxUWl4ZzRVazBLbVEwdTNDc2EiLCJ2IjoxfQ.GjCi6Q_OudRnbWIdXEX80BDlkwob2m_35PoJ7pKi6MY';

    public function testKeyDerivationMatchesPythonVectors(): void
    {
        $keys = HandoffCodeVerifier::deriveKeys(self::SECRET);

        self::assertSame('fe15d237bdb5f946db071f5824cdb37cf7275762a5865ef3e689047d20c39b43', bin2hex($keys['enc']));
        self::assertSame('1c8b5115df163e3d02efda4cccd28e0033d7b3a23865f3f7de9e5e9c3e7f1912', bin2hex($keys['mac']));
    }

    public function testPythonIssuedCodeVerifies(): void
    {
        $verifier = $this->verifier();

        self::assertSame(self::TOKEN, $verifier->verify(self::PYTHON_CODE));
    }

    public function testPhpMintedCodeVerifies(): void
    {
        $verifier = $this->verifier();

        self::assertSame(self::TOKEN, $verifier->verify($this->mint()));
    }

    public function testReplayIsRefused(): void
    {
        $store = new InMemoryConsumedCodeStore();
        $verifier = $this->verifier(store: $store);
        $verifier->verify(self::PYTHON_CODE);

        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('already used');
        try {
            $verifier->verify(self::PYTHON_CODE);
        } finally {
            self::assertSame(1, $store->count());
        }
    }

    public function testConsumedIdsArePurgedAfterExpiry(): void
    {
        $store = new InMemoryConsumedCodeStore();
        $this->verifier(store: $store)->verify(self::PYTHON_CODE);
        self::assertSame(1, $store->count());

        // A later verification (any code) purges ids whose exp has passed.
        $later = new MockClock('@' . (self::ISSUED_AT + 200));
        $laterVerifier = new HandoffCodeVerifier(self::SECRET, $later, $store);
        $laterVerifier->verify($this->mint(issuedAt: self::ISSUED_AT + 190));
        self::assertSame(1, $store->count(), 'the old id is gone, only the new one remains');
    }

    public function testExpiredCodeIsRefused(): void
    {
        $verifier = $this->verifier(now: self::ISSUED_AT + 121);

        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('expired');
        $verifier->verify(self::PYTHON_CODE);
    }

    public function testCodeExactlyAtExpiryIsStillAccepted(): void
    {
        $verifier = $this->verifier(now: self::ISSUED_AT + 120);

        self::assertSame(self::TOKEN, $verifier->verify(self::PYTHON_CODE));
    }

    public function testCodeIssuedTooFarInTheFutureIsRefused(): void
    {
        $verifier = $this->verifier(now: self::ISSUED_AT - 61);

        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('future');
        $verifier->verify(self::PYTHON_CODE);
    }

    public function testClockSkewOfSixtySecondsIsTolerated(): void
    {
        $verifier = $this->verifier(now: self::ISSUED_AT - 60);

        self::assertSame(self::TOKEN, $verifier->verify(self::PYTHON_CODE));
    }

    public function testTamperedMacIsRefused(): void
    {
        [$payload, $mac] = explode('.', self::PYTHON_CODE);
        $flipped = $mac[0] === 'A' ? 'B' : 'A';
        $tampered = $payload . '.' . $flipped . substr($mac, 1);

        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('signature mismatch');
        $this->verifier()->verify($tampered);
    }

    public function testTamperedPayloadIsRefused(): void
    {
        [$payload, $mac] = explode('.', self::PYTHON_CODE);
        $fields = json_decode(self::unb64($payload), true, 512, \JSON_THROW_ON_ERROR);
        $fields['exp'] += 3600;
        $tampered = self::b64(json_encode($fields, \JSON_THROW_ON_ERROR)) . '.' . $mac;

        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('signature mismatch');
        $this->verifier()->verify($tampered);
    }

    public function testWrongSecretIsRefused(): void
    {
        $verifier = $this->verifier(secret: self::OTHER_SECRET);

        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('signature mismatch');
        $verifier->verify(self::PYTHON_CODE);
    }

    public function testBadVersionIsRefused(): void
    {
        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('unsupported version');
        $this->verifier()->verify($this->mint(version: 2));
    }

    public function testTokenOutsideTheContextTokenAlphabetIsRefused(): void
    {
        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('not a context token');
        $this->verifier()->verify($this->mint(token: 'not a token! ../../etc/passwd'));
    }

    public function testTooShortTokenIsRefused(): void
    {
        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('not a context token');
        $this->verifier()->verify($this->mint(token: 'short'));
    }

    public function testLifetimeAboveMaximumIsRefused(): void
    {
        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('lifetime out of range');
        $this->verifier()->verify($this->mint(ttl: 121));
    }

    public function testNonPositiveLifetimeIsRefused(): void
    {
        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('lifetime out of range');
        $this->verifier()->verify($this->mint(ttl: 0));
    }

    public function testMalformedJtiIsRefused(): void
    {
        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('jti malformed');
        $this->verifier()->verify($this->mint(jti: 'ZZ'));
    }

    public function testBoxEncryptedForAnotherJtiIsRefused(): void
    {
        // Valid MAC, but the AES-GCM additional data (jti) does not match the payload's jti.
        $code = $this->mint(jti: str_repeat('a', 32), aadJti: str_repeat('b', 32));

        $this->expectException(HandoffCodeException::class);
        $this->expectExceptionMessage('does not authenticate');
        $this->verifier()->verify($code);
    }

    public function testMalformedCodesAreRefused(): void
    {
        $verifier = $this->verifier();
        foreach (['', 'no-dot', '.mac', 'payload.', '%%%.%%%', self::b64('[]') . '.' . self::b64('x')] as $code) {
            try {
                $verifier->verify($code);
                self::fail(sprintf('code %s must be refused', var_export($code, true)));
            } catch (HandoffCodeException) {
                self::assertTrue(true);
            }
        }
    }

    public function testSecretShorterThan32BytesIsRefusedAtConstruction(): void
    {
        $this->expectException(HandoffConfigurationException::class);
        new HandoffCodeVerifier(str_repeat('x', 31), new MockClock('@' . self::NOW), new InMemoryConsumedCodeStore());
    }

    private function verifier(
        string $secret = self::SECRET,
        int $now = self::NOW,
        ?InMemoryConsumedCodeStore $store = null,
    ): HandoffCodeVerifier {
        return new HandoffCodeVerifier($secret, new MockClock('@' . $now), $store ?? new InMemoryConsumedCodeStore());
    }

    /**
     * PHP re-implementation of `HandoffCodeIssuer.issue` for the negative cases.
     */
    private function mint(
        string $token = self::TOKEN,
        int $issuedAt = self::ISSUED_AT,
        int $ttl = 120,
        int $version = HandoffCodeVerifier::VERSION,
        ?string $jti = null,
        ?string $aadJti = null,
        string $secret = self::SECRET,
    ): string {
        ['enc' => $encKey, 'mac' => $macKey] = HandoffCodeVerifier::deriveKeys($secret);
        $jti ??= bin2hex(random_bytes(16));
        $nonce = random_bytes(12);
        $tag = '';
        $ciphertext = openssl_encrypt($token, 'aes-256-gcm', $encKey, \OPENSSL_RAW_DATA, $nonce, $tag, $aadJti ?? $jti, 16);
        self::assertNotFalse($ciphertext);

        $fields = ['exp' => $issuedAt + $ttl, 'iat' => $issuedAt, 'jti' => $jti, 'tok' => self::b64($nonce . $ciphertext . $tag), 'v' => $version];
        ksort($fields);
        $payload = json_encode($fields, \JSON_THROW_ON_ERROR | \JSON_UNESCAPED_SLASHES);

        return self::b64($payload) . '.' . self::b64(hash_hmac('sha256', $payload, $macKey, true));
    }

    private static function b64(string $raw): string
    {
        return rtrim(strtr(base64_encode($raw), '+/', '-_'), '=');
    }

    private static function unb64(string $text): string
    {
        return (string) base64_decode(strtr($text, '-_', '+/') . str_repeat('=', (4 - \strlen($text) % 4) % 4), true);
    }
}
