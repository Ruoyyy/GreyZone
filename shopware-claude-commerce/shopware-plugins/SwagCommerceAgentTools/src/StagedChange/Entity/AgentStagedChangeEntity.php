<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange\Entity;

use Shopware\Core\Framework\DataAbstractionLayer\Entity;
use Shopware\Core\Framework\DataAbstractionLayer\EntityIdTrait;
use Shopware\Core\System\SalesChannel\SalesChannelEntity;

class AgentStagedChangeEntity extends Entity
{
    use EntityIdTrait;

    protected string $kind;

    protected string $status;

    protected string $summary;

    protected ?string $note = null;

    protected string $targetEntity;

    /** @var list<array<string, mixed>> */
    protected array $items = [];

    /** @var list<array<string, mixed>> */
    protected array $payload = [];

    /** @var list<array<string, mixed>>|null */
    protected ?array $preview = null;

    /** @var list<mixed>|null host-side guardrail lines (strings) plus apply-time notes */
    protected ?array $guardrailNotes = null;

    protected string $createdBy;

    protected string $createdByKind;

    protected ?string $appliedBy = null;

    protected ?\DateTimeInterface $appliedAt = null;

    protected ?string $discardedBy = null;

    protected ?\DateTimeInterface $discardedAt = null;

    protected ?string $errorMessage = null;

    protected ?string $salesChannelId = null;

    protected ?string $currency = null;

    protected ?float $marginBeforePct = null;

    protected ?float $marginAfterPct = null;

    protected ?float $minMarginPct = null;

    protected ?SalesChannelEntity $salesChannel = null;

    public function getKind(): string
    {
        return $this->kind;
    }

    public function setKind(string $kind): void
    {
        $this->kind = $kind;
    }

    public function getStatus(): string
    {
        return $this->status;
    }

    public function setStatus(string $status): void
    {
        $this->status = $status;
    }

    public function getSummary(): string
    {
        return $this->summary;
    }

    public function setSummary(string $summary): void
    {
        $this->summary = $summary;
    }

    public function getNote(): ?string
    {
        return $this->note;
    }

    public function setNote(?string $note): void
    {
        $this->note = $note;
    }

    public function getTargetEntity(): string
    {
        return $this->targetEntity;
    }

    public function setTargetEntity(string $targetEntity): void
    {
        $this->targetEntity = $targetEntity;
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function getItems(): array
    {
        return $this->items;
    }

    /**
     * @param list<array<string, mixed>> $items
     */
    public function setItems(array $items): void
    {
        $this->items = $items;
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function getPayload(): array
    {
        return $this->payload;
    }

    /**
     * @param list<array<string, mixed>> $payload
     */
    public function setPayload(array $payload): void
    {
        $this->payload = $payload;
    }

    /**
     * @return list<array<string, mixed>>|null
     */
    public function getPreview(): ?array
    {
        return $this->preview;
    }

    /**
     * @param list<array<string, mixed>>|null $preview
     */
    public function setPreview(?array $preview): void
    {
        $this->preview = $preview;
    }

    /**
     * @return list<mixed>|null
     */
    public function getGuardrailNotes(): ?array
    {
        return $this->guardrailNotes;
    }

    /**
     * @param list<mixed>|null $guardrailNotes
     */
    public function setGuardrailNotes(?array $guardrailNotes): void
    {
        $this->guardrailNotes = $guardrailNotes;
    }

    public function getCreatedBy(): string
    {
        return $this->createdBy;
    }

    public function setCreatedBy(string $createdBy): void
    {
        $this->createdBy = $createdBy;
    }

    public function getCreatedByKind(): string
    {
        return $this->createdByKind;
    }

    public function setCreatedByKind(string $createdByKind): void
    {
        $this->createdByKind = $createdByKind;
    }

    public function getAppliedBy(): ?string
    {
        return $this->appliedBy;
    }

    public function setAppliedBy(?string $appliedBy): void
    {
        $this->appliedBy = $appliedBy;
    }

    public function getAppliedAt(): ?\DateTimeInterface
    {
        return $this->appliedAt;
    }

    public function setAppliedAt(?\DateTimeInterface $appliedAt): void
    {
        $this->appliedAt = $appliedAt;
    }

    public function getDiscardedBy(): ?string
    {
        return $this->discardedBy;
    }

    public function setDiscardedBy(?string $discardedBy): void
    {
        $this->discardedBy = $discardedBy;
    }

    public function getDiscardedAt(): ?\DateTimeInterface
    {
        return $this->discardedAt;
    }

    public function setDiscardedAt(?\DateTimeInterface $discardedAt): void
    {
        $this->discardedAt = $discardedAt;
    }

    public function getErrorMessage(): ?string
    {
        return $this->errorMessage;
    }

    public function setErrorMessage(?string $errorMessage): void
    {
        $this->errorMessage = $errorMessage;
    }

    public function getSalesChannelId(): ?string
    {
        return $this->salesChannelId;
    }

    public function setSalesChannelId(?string $salesChannelId): void
    {
        $this->salesChannelId = $salesChannelId;
    }

    public function getCurrency(): ?string
    {
        return $this->currency;
    }

    public function setCurrency(?string $currency): void
    {
        $this->currency = $currency;
    }

    public function getMarginBeforePct(): ?float
    {
        return $this->marginBeforePct;
    }

    public function setMarginBeforePct(?float $marginBeforePct): void
    {
        $this->marginBeforePct = $marginBeforePct;
    }

    public function getMarginAfterPct(): ?float
    {
        return $this->marginAfterPct;
    }

    public function setMarginAfterPct(?float $marginAfterPct): void
    {
        $this->marginAfterPct = $marginAfterPct;
    }

    public function getMinMarginPct(): ?float
    {
        return $this->minMarginPct;
    }

    public function setMinMarginPct(?float $minMarginPct): void
    {
        $this->minMarginPct = $minMarginPct;
    }

    public function getSalesChannel(): ?SalesChannelEntity
    {
        return $this->salesChannel;
    }

    public function setSalesChannel(?SalesChannelEntity $salesChannel): void
    {
        $this->salesChannel = $salesChannel;
    }

    /**
     * Compact representation for MCP tool responses.
     *
     * @return array<string, mixed>
     */
    public function toToolArray(bool $includePayload = false): array
    {
        $data = [
            'changeId' => $this->getId(),
            'kind' => $this->kind,
            'status' => $this->status,
            'summary' => $this->summary,
            'note' => $this->note,
            'targetEntity' => $this->targetEntity,
            'itemCount' => \count($this->items),
            'items' => $this->preview ?? [],
            'guardrailNotes' => $this->guardrailNotes ?? [],
            'createdBy' => $this->createdBy,
            'createdByKind' => $this->createdByKind,
            'createdAt' => $this->createdAt?->format(\DATE_ATOM),
            'appliedBy' => $this->appliedBy,
            'appliedAt' => $this->appliedAt?->format(\DATE_ATOM),
            'discardedBy' => $this->discardedBy,
            'discardedAt' => $this->discardedAt?->format(\DATE_ATOM),
            'errorMessage' => $this->errorMessage,
            'salesChannelId' => $this->salesChannelId,
            'currency' => $this->currency,
            'marginBeforePct' => $this->marginBeforePct,
            'marginAfterPct' => $this->marginAfterPct,
            'minMarginPct' => $this->minMarginPct,
        ];

        if ($includePayload) {
            $data['requestedItems'] = $this->items;
            $data['payload'] = $this->payload;
        }

        return $data;
    }
}
