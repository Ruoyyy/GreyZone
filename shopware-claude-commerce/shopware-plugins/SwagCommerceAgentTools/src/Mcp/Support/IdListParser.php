<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\Support;

use Shopware\Core\Framework\Uuid\Uuid;

/**
 * MCP tool parameters are limited to scalars, so list-valued arguments arrive as
 * a JSON array string or a comma-separated string. This parser accepts both and
 * validates every entry as a Shopware UUID (32 hex chars).
 */
final class IdListParser
{
    /**
     * @return list<string>|string the validated ID list, or an error message
     */
    public static function parse(string $raw, string $parameterName, int $maxItems): array|string
    {
        $trimmed = trim($raw);
        if ($trimmed === '') {
            return \sprintf('"%s" must not be empty. Pass a JSON array of product UUIDs or a comma-separated list.', $parameterName);
        }

        $values = null;
        if (str_starts_with($trimmed, '[')) {
            try {
                $decoded = json_decode($trimmed, true, 8, \JSON_THROW_ON_ERROR);
            } catch (\JsonException $e) {
                return \sprintf('"%s" is not valid JSON: %s', $parameterName, $e->getMessage());
            }
            if (!\is_array($decoded)) {
                return \sprintf('"%s" must be a JSON array of UUID strings.', $parameterName);
            }
            $values = $decoded;
        } else {
            $values = explode(',', $trimmed);
        }

        $ids = [];
        foreach ($values as $value) {
            if (!\is_string($value)) {
                return \sprintf('"%s" must contain only UUID strings.', $parameterName);
            }
            $id = strtolower(trim($value));
            if ($id === '') {
                continue;
            }
            if (!Uuid::isValid($id)) {
                return \sprintf('"%s" contains an invalid UUID: "%s". Use the IDs returned by a previous product search.', $parameterName, $value);
            }
            $ids[$id] = true;
        }

        if ($ids === []) {
            return \sprintf('"%s" must contain at least one UUID.', $parameterName);
        }

        if (\count($ids) > $maxItems) {
            return \sprintf('"%s" may contain at most %d IDs (got %d).', $parameterName, $maxItems, \count($ids));
        }

        return array_keys($ids);
    }
}
