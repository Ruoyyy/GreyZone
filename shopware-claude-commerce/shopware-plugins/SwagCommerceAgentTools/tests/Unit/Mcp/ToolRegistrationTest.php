<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Mcp;

use Mcp\Capability\Attribute\McpTool;
use PHPUnit\Framework\Attributes\CoversNothing;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Shopware\Core\Framework\Mcp\Attribute\McpToolGroup;
use Shopware\Core\Framework\Mcp\Tool\McpToolResponse;
use Swag\CommerceAgentTools\Mcp\Admin\BusinessSnapshotTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeApplyTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeDiscardTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeListTool;
use Swag\CommerceAgentTools\Mcp\Admin\ChangeStageTool;
use Swag\CommerceAgentTools\Mcp\Admin\MetricsSeriesTool;
use Swag\CommerceAgentTools\Mcp\StoreApi\DisclosureTool;
use Swag\CommerceAgentTools\Mcp\StoreApi\FulfillmentOptionsTool;
use Swag\CommerceAgentTools\Mcp\StoreApi\PolicySearchTool;

/**
 * Attribute- and registration-level guards: tool names, groups, the service
 * tag per endpoint, and that every write tool defaults to dryRun=true.
 *
 * @internal
 */
#[CoversNothing]
final class ToolRegistrationTest extends TestCase
{
    private const NAME_PATTERN = '/^[a-zA-Z0-9_-]+$/';

    /**
     * @param class-string $toolClass
     */
    #[DataProvider('toolProvider')]
    public function testToolAttributes(string $toolClass, string $expectedName, string $expectedGroup, string $expectedTag): void
    {
        $reflection = new \ReflectionClass($toolClass);

        static::assertTrue($reflection->isSubclassOf(McpToolResponse::class), $toolClass . ' must extend McpToolResponse');

        $toolAttributes = $reflection->getAttributes(McpTool::class);
        static::assertCount(1, $toolAttributes, $toolClass . ' must carry exactly one #[McpTool] on the class');
        $tool = $toolAttributes[0]->newInstance();
        static::assertSame($expectedName, $tool->name);
        static::assertMatchesRegularExpression(self::NAME_PATTERN, $tool->name, 'tool names may only contain a-zA-Z0-9_-');
        static::assertStringStartsNotWith('shopware-', $tool->name, 'the shopware- prefix is reserved for core');
        static::assertIsString($tool->description);
        static::assertGreaterThan(80, \strlen($tool->description), 'descriptions are the routing surface; keep them substantive');

        $groupAttributes = $reflection->getAttributes(McpToolGroup::class);
        static::assertCount(1, $groupAttributes, $toolClass . ' must declare exactly one #[McpToolGroup]');
        static::assertSame($expectedGroup, $groupAttributes[0]->newInstance()->group);

        static::assertFalse($reflection->getMethod('__invoke')->getAttributes(McpTool::class) !== [], '#[McpTool] must not be on __invoke()');

        static::assertSame($expectedTag, self::serviceTags()[$toolClass] ?? null, $toolClass . ' must be tagged in services.xml');
    }

    /**
     * @return iterable<string, array{class-string, string, string, string}>
     */
    public static function toolProvider(): iterable
    {
        yield 'policy search' => [PolicySearchTool::class, 'shopping-policy-search', 'agent-shopping', 'shopware.store_api_mcp.tool'];
        yield 'disclosure' => [DisclosureTool::class, 'shopping-disclosure', 'agent-shopping', 'shopware.store_api_mcp.tool'];
        yield 'fulfillment' => [FulfillmentOptionsTool::class, 'shopping-fulfillment-options', 'agent-shopping', 'shopware.store_api_mcp.tool'];
        yield 'change stage' => [ChangeStageTool::class, 'agent-change-stage', 'agent-merchant', 'shopware.mcp.tool'];
        yield 'change list' => [ChangeListTool::class, 'agent-change-list', 'agent-merchant', 'shopware.mcp.tool'];
        yield 'change apply' => [ChangeApplyTool::class, 'agent-change-apply', 'agent-merchant', 'shopware.mcp.tool'];
        yield 'change discard' => [ChangeDiscardTool::class, 'agent-change-discard', 'agent-merchant', 'shopware.mcp.tool'];
        yield 'business snapshot' => [BusinessSnapshotTool::class, 'agent-business-snapshot', 'agent-merchant', 'shopware.mcp.tool'];
        yield 'metrics series' => [MetricsSeriesTool::class, 'agent-metrics-series', 'agent-merchant', 'shopware.mcp.tool'];
    }

    /**
     * @param class-string $toolClass
     */
    #[DataProvider('writeToolProvider')]
    public function testWriteToolsDefaultToDryRun(string $toolClass): void
    {
        $parameters = (new \ReflectionClass($toolClass))->getMethod('__invoke')->getParameters();
        $dryRun = null;
        foreach ($parameters as $parameter) {
            if ($parameter->getName() === 'dryRun') {
                $dryRun = $parameter;
            }
        }

        static::assertNotNull($dryRun, $toolClass . ' must expose a dryRun parameter');
        static::assertTrue($dryRun->isDefaultValueAvailable());
        static::assertTrue($dryRun->getDefaultValue());
    }

    /**
     * @return iterable<string, array{class-string}>
     */
    public static function writeToolProvider(): iterable
    {
        yield 'stage' => [ChangeStageTool::class];
        yield 'apply' => [ChangeApplyTool::class];
        yield 'discard' => [ChangeDiscardTool::class];
    }

    public function testInvokeParametersAreScalarsOnly(): void
    {
        foreach (self::toolProvider() as [$toolClass]) {
            foreach ((new \ReflectionClass($toolClass))->getMethod('__invoke')->getParameters() as $parameter) {
                $type = $parameter->getType();
                static::assertInstanceOf(\ReflectionNamedType::class, $type, $toolClass . '::' . $parameter->getName());
                static::assertContains($type->getName(), ['string', 'int', 'float', 'bool'], \sprintf('%s::$%s must be a scalar for the MCP JSON schema', $toolClass, $parameter->getName()));
            }
        }
    }

    /**
     * @return array<string, string> tool class => shopware tag
     */
    private static function serviceTags(): array
    {
        $xml = simplexml_load_file(\dirname(__DIR__, 3) . '/src/Resources/config/services.xml');
        static::assertNotFalse($xml);

        $tags = [];
        foreach ($xml->services->service as $service) {
            $id = (string) $service['id'];
            foreach ($service->tag as $tag) {
                $name = (string) $tag['name'];
                if (str_contains($name, 'mcp.tool')) {
                    $tags[$id] = $name;
                }
            }
        }

        return $tags;
    }
}
