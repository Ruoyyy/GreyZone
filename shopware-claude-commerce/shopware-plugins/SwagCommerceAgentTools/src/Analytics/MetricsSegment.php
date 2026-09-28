<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Analytics;

use Shopware\Core\Framework\Uuid\Uuid;

/**
 * Optional slice of the order data: `category:<uuid>` or `sales_channel:<uuid>`.
 */
final class MetricsSegment
{
    public const TYPE_CATEGORY = 'category';
    public const TYPE_SALES_CHANNEL = 'sales_channel';

    private function __construct(
        public readonly string $type,
        public readonly string $id,
    ) {
    }

    /**
     * @throws \InvalidArgumentException
     */
    public static function parse(string $segment): ?self
    {
        $segment = trim($segment);
        if ($segment === '') {
            return null;
        }

        $parts = explode(':', $segment, 2);
        if (\count($parts) !== 2) {
            throw new \InvalidArgumentException('Segments have the form "category:<uuid>" or "sales_channel:<uuid>".');
        }

        $type = strtolower(trim($parts[0]));
        $id = strtolower(trim($parts[1]));

        if (!\in_array($type, [self::TYPE_CATEGORY, self::TYPE_SALES_CHANNEL], true)) {
            throw new \InvalidArgumentException(\sprintf('Unknown segment type "%s". Supported: category, sales_channel.', $type));
        }
        if (!Uuid::isValid($id)) {
            throw new \InvalidArgumentException(\sprintf('Segment "%s" needs a UUID after the colon.', $type));
        }

        return new self($type, $id);
    }

    public function toString(): string
    {
        return $this->type . ':' . $this->id;
    }
}
