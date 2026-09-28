<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Fulfillment;

use Shopware\Core\System\DeliveryTime\DeliveryTimeEntity;

/**
 * Turns a delivery time entity into the ETA structure of a fulfillment option.
 */
class DeliveryTimeFormatter
{
    /**
     * @return array{min: int, max: int, unit: string, text: string|null}|null
     */
    public function format(?DeliveryTimeEntity $deliveryTime): ?array
    {
        if ($deliveryTime === null) {
            return null;
        }

        $name = $deliveryTime->getTranslation('name');

        return [
            'min' => $deliveryTime->getMin(),
            'max' => $deliveryTime->getMax(),
            'unit' => $deliveryTime->getUnit(),
            'text' => \is_string($name) && $name !== '' ? $name : null,
        ];
    }

    /**
     * Widens an ETA so that it covers both ranges (used when several products
     * with different delivery times ship with the same method).
     *
     * @param array{min: int, max: int, unit: string, text: string|null}|null $current
     * @param array{min: int, max: int, unit: string, text: string|null}|null $candidate
     *
     * @return array{min: int, max: int, unit: string, text: string|null}|null
     */
    public function widen(?array $current, ?array $candidate): ?array
    {
        if ($candidate === null) {
            return $current;
        }
        if ($current === null) {
            return $candidate;
        }
        if ($current['unit'] !== $candidate['unit']) {
            // Mixed units cannot be merged meaningfully; keep the slower estimate.
            return $this->rank($candidate) > $this->rank($current) ? $candidate : $current;
        }

        $min = min($current['min'], $candidate['min']);
        $max = max($current['max'], $candidate['max']);
        $text = $min === $current['min'] && $max === $current['max'] ? $current['text'] : null;

        return ['min' => $min, 'max' => $max, 'unit' => $current['unit'], 'text' => $text];
    }

    /**
     * @param array{min: int, max: int, unit: string, text: string|null} $eta
     */
    private function rank(array $eta): int
    {
        $unitFactor = match ($eta['unit']) {
            DeliveryTimeEntity::DELIVERY_TIME_HOUR => 1,
            DeliveryTimeEntity::DELIVERY_TIME_DAY => 24,
            DeliveryTimeEntity::DELIVERY_TIME_WEEK => 24 * 7,
            DeliveryTimeEntity::DELIVERY_TIME_MONTH => 24 * 30,
            DeliveryTimeEntity::DELIVERY_TIME_YEAR => 24 * 365,
            default => 24,
        };

        return $eta['max'] * $unitFactor;
    }
}
