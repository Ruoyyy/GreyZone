<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Event;

use Shopware\Core\Framework\Event\BusinessEventCollector;
use Shopware\Core\Framework\Event\BusinessEventCollectorEvent;
use Symfony\Component\EventDispatcher\EventSubscriberInterface;

/**
 * Publishes the agent change events to the Flow Builder trigger list and to
 * the MCP resource `shopware://business-events`.
 */
class BusinessEventCollectorSubscriber implements EventSubscriberInterface
{
    /** @var list<class-string<AbstractAgentChangeEvent>> */
    public const EVENT_CLASSES = [
        AgentChangeStagedEvent::class,
        AgentChangeAppliedEvent::class,
    ];

    public function __construct(
        private readonly BusinessEventCollector $businessEventCollector,
    ) {
    }

    public static function getSubscribedEvents(): array
    {
        return [
            BusinessEventCollectorEvent::NAME => ['onCollectBusinessEvents', 1000],
        ];
    }

    public function onCollectBusinessEvents(BusinessEventCollectorEvent $event): void
    {
        $collection = $event->getCollection();

        foreach (self::EVENT_CLASSES as $class) {
            $definition = $this->businessEventCollector->define($class);
            if ($definition === null) {
                continue;
            }
            $collection->set($definition->getName(), $definition);
        }
    }
}
