<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Event;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\TestCase;
use Shopware\Core\Content\Flow\Dispatching\Aware\ScalarValuesAware;
use Shopware\Core\Framework\Event\BusinessEventCollectorEvent;
use Shopware\Core\Framework\Event\EventData\ScalarValueType;
use Shopware\Core\Framework\Event\FlowEventAware;
use Shopware\Core\Framework\Event\MailAware;
use Swag\CommerceAgentTools\Event\AbstractAgentChangeEvent;
use Swag\CommerceAgentTools\Event\AgentChangeAppliedEvent;
use Swag\CommerceAgentTools\Event\AgentChangeStagedEvent;
use Swag\CommerceAgentTools\Event\BusinessEventCollectorSubscriber;
use Swag\CommerceAgentTools\Tests\Unit\Support\ChangeFixtures;

/**
 * @internal
 */
#[CoversClass(AbstractAgentChangeEvent::class)]
#[CoversClass(AgentChangeStagedEvent::class)]
#[CoversClass(AgentChangeAppliedEvent::class)]
#[CoversClass(BusinessEventCollectorSubscriber::class)]
class AgentChangeEventsTest extends TestCase
{
    public function testEventNamesAndInterfaces(): void
    {
        foreach (BusinessEventCollectorSubscriber::EVENT_CLASSES as $class) {
            $reflection = new \ReflectionClass($class);
            static::assertTrue($reflection->implementsInterface(FlowEventAware::class));
            static::assertTrue($reflection->implementsInterface(MailAware::class));
            static::assertTrue($reflection->implementsInterface(ScalarValuesAware::class));

            // BusinessEventCollector::define() instantiates without constructor and calls getName().
            $instance = $reflection->newInstanceWithoutConstructor();
            static::assertInstanceOf(FlowEventAware::class, $instance);
            static::assertMatchesRegularExpression('/^swag\.agent\.change\.(staged|applied)$/', $instance->getName());
        }

        static::assertSame('swag.agent.change.staged', AgentChangeStagedEvent::EVENT_NAME);
        static::assertSame('swag.agent.change.applied', AgentChangeAppliedEvent::EVENT_NAME);
    }

    public function testAvailableDataMatchesValues(): void
    {
        $context = ChangeFixtures::adminContext([]);
        $event = new AgentChangeStagedEvent(ChangeFixtures::change(), $context, ChangeFixtures::INTEGRATION_ID, 'integration', ['ops@example.com' => 'Ops']);

        $available = AgentChangeStagedEvent::getAvailableData()->toArray();
        $values = $event->getValues();

        static::assertSame(array_keys($available), array_keys($values), 'every declared field must be delivered and vice versa');
        static::assertSame(ScalarValueType::TYPE_INT, $available['itemCount']['type']);
        static::assertSame(ChangeFixtures::CHANGE_ID, $values['changeId']);
        static::assertSame('Restock stool by 25', $values['summary']);
        static::assertSame('product', $values['targetEntity']);
        static::assertNull($values['salesChannelId']);
        static::assertSame($context, $event->getContext());
        static::assertSame(['ops@example.com' => 'Ops'], $event->getMailStruct()->getRecipients());
        static::assertSame($event->getMailStruct(), $event->getMailStruct(), 'mail struct is memoised');
        static::assertNull($event->getSalesChannelId());
    }

    public function testSubscriberListensToTheCollectorEvent(): void
    {
        $subscribed = BusinessEventCollectorSubscriber::getSubscribedEvents();

        static::assertArrayHasKey(BusinessEventCollectorEvent::NAME, $subscribed);
        static::assertSame('onCollectBusinessEvents', $subscribed[BusinessEventCollectorEvent::NAME][0]);
    }
}
