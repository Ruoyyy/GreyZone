<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Event;

use Shopware\Core\Content\Flow\Dispatching\Aware\ScalarValuesAware;
use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\Event\EventData\EventDataCollection;
use Shopware\Core\Framework\Event\EventData\MailRecipientStruct;
use Shopware\Core\Framework\Event\EventData\ScalarValueType;
use Shopware\Core\Framework\Event\FlowEventAware;
use Shopware\Core\Framework\Event\MailAware;
use Swag\CommerceAgentTools\StagedChange\Entity\AgentStagedChangeEntity;
use Symfony\Contracts\EventDispatcher\Event;

/**
 * Base class of the Flow Builder triggers for the staged-change workflow.
 *
 * MailAware makes the "Send e-mail" action work out of the box (recipients come
 * from the plugin config), ScalarValuesAware exposes the change facts to
 * templates and rule conditions ({{ changeId }}, {{ kind }}, {{ summary }}, ...).
 */
abstract class AbstractAgentChangeEvent extends Event implements FlowEventAware, MailAware, ScalarValuesAware
{
    public const DATA_CHANGE_ID = 'changeId';
    public const DATA_KIND = 'kind';
    public const DATA_STATUS = 'status';
    public const DATA_SUMMARY = 'summary';
    public const DATA_ITEM_COUNT = 'itemCount';
    public const DATA_ACTOR_ID = 'actorId';
    public const DATA_ACTOR_KIND = 'actorKind';
    public const DATA_TARGET_ENTITY = 'targetEntity';

    private ?MailRecipientStruct $mailRecipientStruct = null;

    /**
     * @param array<string, string> $mailRecipients e-mail address => display name
     */
    public function __construct(
        private readonly AgentStagedChangeEntity $change,
        private readonly Context $context,
        private readonly string $actorId,
        private readonly string $actorKind,
        private readonly array $mailRecipients = [],
    ) {
    }

    abstract public function getName(): string;

    public static function getAvailableData(): EventDataCollection
    {
        return (new EventDataCollection())
            ->add(self::DATA_CHANGE_ID, new ScalarValueType(ScalarValueType::TYPE_STRING))
            ->add(self::DATA_KIND, new ScalarValueType(ScalarValueType::TYPE_STRING))
            ->add(self::DATA_STATUS, new ScalarValueType(ScalarValueType::TYPE_STRING))
            ->add(self::DATA_SUMMARY, new ScalarValueType(ScalarValueType::TYPE_STRING))
            ->add(self::DATA_ITEM_COUNT, new ScalarValueType(ScalarValueType::TYPE_INT))
            ->add(self::DATA_ACTOR_ID, new ScalarValueType(ScalarValueType::TYPE_STRING))
            ->add(self::DATA_ACTOR_KIND, new ScalarValueType(ScalarValueType::TYPE_STRING))
            ->add(self::DATA_TARGET_ENTITY, new ScalarValueType(ScalarValueType::TYPE_STRING))
            ->add(MailAware::SALES_CHANNEL_ID, new ScalarValueType(ScalarValueType::TYPE_STRING));
    }

    public function getContext(): Context
    {
        return $this->context;
    }

    public function getChange(): AgentStagedChangeEntity
    {
        return $this->change;
    }

    public function getActorId(): string
    {
        return $this->actorId;
    }

    public function getActorKind(): string
    {
        return $this->actorKind;
    }

    /**
     * @return array<string, mixed>
     */
    public function getValues(): array
    {
        return [
            self::DATA_CHANGE_ID => $this->change->getId(),
            self::DATA_KIND => $this->change->getKind(),
            self::DATA_STATUS => $this->change->getStatus(),
            self::DATA_SUMMARY => $this->change->getSummary(),
            self::DATA_ITEM_COUNT => \count($this->change->getItems()),
            self::DATA_ACTOR_ID => $this->actorId,
            self::DATA_ACTOR_KIND => $this->actorKind,
            self::DATA_TARGET_ENTITY => $this->change->getTargetEntity(),
            MailAware::SALES_CHANNEL_ID => $this->change->getSalesChannelId(),
        ];
    }

    public function getMailStruct(): MailRecipientStruct
    {
        if (!$this->mailRecipientStruct instanceof MailRecipientStruct) {
            $this->mailRecipientStruct = new MailRecipientStruct($this->mailRecipients);
        }

        return $this->mailRecipientStruct;
    }

    public function getSalesChannelId(): ?string
    {
        return $this->change->getSalesChannelId();
    }
}
