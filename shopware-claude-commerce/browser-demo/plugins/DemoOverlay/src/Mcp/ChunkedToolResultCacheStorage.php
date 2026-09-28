<?php declare(strict_types=1);

namespace CommerceAgents\DemoOverlay\Mcp;

use Doctrine\DBAL\Connection;
use Psr\Clock\ClockInterface;
use Shopware\Core\Defaults;
use Shopware\Core\Framework\Mcp\ToolResultCacheStorage;
use Shopware\Core\Framework\Uuid\Uuid;

/**
 * Browser-demo replacement for the MCP tool-result cache writer.
 *
 * MariaDB WASM (lite4mariadb) aborts any statement whose SQL text carries a
 * string literal larger than roughly 150 KB with "Thread stack overrun", and the
 * playground's DBAL bridge inlines all parameters into the statement text.
 * Shopware only persists MCP tool results that exceed 100 KB, so every write
 * through the stock storage class would hit that limit.
 *
 * This subclass keeps the schema and the read path untouched and merely splits
 * the write into an INSERT of the first chunk followed by CONCAT updates, so no
 * single statement exceeds CHUNK_BYTES of literal text.
 */
class ChunkedToolResultCacheStorage extends ToolResultCacheStorage
{
    /**
     * Upper bound of literal bytes per statement. Measured limit is ~150 KB;
     * 96 KB leaves headroom for SQL escaping (quotes, backslashes, multibyte).
     */
    public const CHUNK_BYTES = 96 * 1024;

    public function __construct(
        private readonly Connection $chunkedConnection,
        private readonly ClockInterface $chunkedClock,
    ) {
        parent::__construct($chunkedConnection, $chunkedClock);
    }

    public function store(string $sessionId, string $content, string $mimeType = 'application/json'): string
    {
        $chunks = self::splitUtf8($content, self::CHUNK_BYTES);
        \assert($chunks !== [] && implode('', $chunks) === $content);

        $id = Uuid::randomBytes();

        $this->chunkedConnection->transactional(function (Connection $connection) use ($id, $sessionId, $mimeType, $chunks): void {
            $connection->insert('mcp_tool_result_cache', [
                'id' => $id,
                'session_id' => $sessionId,
                'mime_type' => $mimeType,
                'content' => array_shift($chunks),
                'created_at' => $this->chunkedClock->now()->format(Defaults::STORAGE_DATE_TIME_FORMAT),
            ]);

            foreach ($chunks as $chunk) {
                $connection->executeStatement(
                    'UPDATE `mcp_tool_result_cache` SET `content` = CONCAT(`content`, :chunk) WHERE `id` = :id',
                    ['chunk' => $chunk, 'id' => $id],
                );
            }
        });

        return Uuid::fromBytesToHex($id);
    }

    /**
     * Splits $content into pieces of at most $maxBytes without cutting through a
     * UTF-8 sequence (CONCAT on a utf8mb4 column would otherwise reject the chunk).
     *
     * @return non-empty-list<string>
     */
    public static function splitUtf8(string $content, int $maxBytes): array
    {
        \assert($maxBytes >= 4);

        if ($content === '') {
            return [''];
        }

        $chunks = [];
        $length = \strlen($content);
        $offset = 0;

        while ($offset < $length) {
            $end = min($offset + $maxBytes, $length);

            if ($end < $length) {
                // Step back while $end points at a UTF-8 continuation byte (10xxxxxx).
                while ($end > $offset && (\ord($content[$end]) & 0xC0) === 0x80) {
                    --$end;
                }
                if ($end === $offset) {
                    // Pathological input (not valid UTF-8); fall back to a hard cut.
                    $end = min($offset + $maxBytes, $length);
                }
            }

            $chunks[] = substr($content, $offset, $end - $offset);
            $offset = $end;
        }

        return $chunks;
    }
}
