<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\StagedChange\Entity;

use Shopware\Core\Framework\DataAbstractionLayer\EntityDefinition;
use Shopware\Core\Framework\DataAbstractionLayer\Field\DateTimeField;
use Shopware\Core\Framework\DataAbstractionLayer\Field\FkField;
use Shopware\Core\Framework\DataAbstractionLayer\Field\FloatField;
use Shopware\Core\Framework\DataAbstractionLayer\Field\Flag\ApiAware;
use Shopware\Core\Framework\DataAbstractionLayer\Field\Flag\PrimaryKey;
use Shopware\Core\Framework\DataAbstractionLayer\Field\Flag\Required;
use Shopware\Core\Framework\DataAbstractionLayer\Field\IdField;
use Shopware\Core\Framework\DataAbstractionLayer\Field\JsonField;
use Shopware\Core\Framework\DataAbstractionLayer\Field\LongTextField;
use Shopware\Core\Framework\DataAbstractionLayer\Field\ManyToOneAssociationField;
use Shopware\Core\Framework\DataAbstractionLayer\Field\StringField;
use Shopware\Core\Framework\DataAbstractionLayer\FieldCollection;
use Shopware\Core\System\SalesChannel\SalesChannelDefinition;
use Swag\CommerceAgentTools\StagedChange\ChangeStatus;

/**
 * Ledger of agent-proposed changes. A row is written when a merchant agent
 * stages a change, and updated when an approver applies or discards it.
 * The live entities (product, ...) are only touched by the apply step.
 */
class AgentStagedChangeDefinition extends EntityDefinition
{
    public const ENTITY_NAME = 'swag_agent_staged_change';

    public function getEntityName(): string
    {
        return self::ENTITY_NAME;
    }

    public function getEntityClass(): string
    {
        return AgentStagedChangeEntity::class;
    }

    public function getCollectionClass(): string
    {
        return AgentStagedChangeCollection::class;
    }

    public function getDefaults(): array
    {
        return [
            'status' => ChangeStatus::Staged->value,
        ];
    }

    protected function defineFields(): FieldCollection
    {
        return new FieldCollection([
            (new IdField('id', 'id'))->addFlags(new ApiAware(), new PrimaryKey(), new Required()),
            (new StringField('kind', 'kind'))->addFlags(new ApiAware(), new Required()),
            (new StringField('status', 'status'))->addFlags(new ApiAware(), new Required()),
            (new LongTextField('summary', 'summary'))->addFlags(new ApiAware(), new Required()),
            (new LongTextField('note', 'note'))->addFlags(new ApiAware()),
            (new StringField('target_entity', 'targetEntity'))->addFlags(new ApiAware(), new Required()),
            (new JsonField('items', 'items'))->addFlags(new ApiAware(), new Required()),
            (new JsonField('payload', 'payload'))->addFlags(new ApiAware(), new Required()),
            (new JsonField('preview', 'preview'))->addFlags(new ApiAware()),
            (new JsonField('guardrail_notes', 'guardrailNotes'))->addFlags(new ApiAware()),
            (new StringField('created_by', 'createdBy'))->addFlags(new ApiAware(), new Required()),
            (new StringField('created_by_kind', 'createdByKind'))->addFlags(new ApiAware(), new Required()),
            (new StringField('applied_by', 'appliedBy'))->addFlags(new ApiAware()),
            (new DateTimeField('applied_at', 'appliedAt'))->addFlags(new ApiAware()),
            (new StringField('discarded_by', 'discardedBy'))->addFlags(new ApiAware()),
            (new DateTimeField('discarded_at', 'discardedAt'))->addFlags(new ApiAware()),
            (new LongTextField('error_message', 'errorMessage'))->addFlags(new ApiAware()),
            (new FkField('sales_channel_id', 'salesChannelId', SalesChannelDefinition::class))->addFlags(new ApiAware()),
            (new StringField('currency', 'currency'))->addFlags(new ApiAware()),
            (new FloatField('margin_before_pct', 'marginBeforePct'))->addFlags(new ApiAware()),
            (new FloatField('margin_after_pct', 'marginAfterPct'))->addFlags(new ApiAware()),
            (new FloatField('min_margin_pct', 'minMarginPct'))->addFlags(new ApiAware()),
            new ManyToOneAssociationField('salesChannel', 'sales_channel_id', SalesChannelDefinition::class, 'id', false),
        ]);
    }
}
