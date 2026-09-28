<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Fulfillment;

final class ShippingFee
{
    /**
     * @param bool $estimated true when the fee was derived from the shipping-method price matrix
     *                        instead of the calculated delivery of the current cart
     */
    public function __construct(
        public readonly float $amount,
        public readonly string $currency,
        public readonly bool $estimated,
    ) {
    }

    /**
     * @return array{amount: float, currency: string, estimated: bool}
     */
    public function toArray(): array
    {
        return [
            'amount' => round($this->amount, 2),
            'currency' => $this->currency,
            'estimated' => $this->estimated,
        ];
    }
}
